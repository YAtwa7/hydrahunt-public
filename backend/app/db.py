"""SQLite persistence layer.

Schema notes:
- jobs: every posting/lead ever seen. posted_at = when the employer posted it,
  first_seen_at = when WE discovered it. The delta is our freshness edge.
- dorks: DorkLab population with fitness tracking.
- scans: run history for the source-coverage metrics page.
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_hash TEXT UNIQUE NOT NULL,          -- hash(company|title|location)
    source TEXT NOT NULL,                     -- linkedin | himalayas | greenhouse | ...
    source_url TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT DEFAULT '',
    description TEXT DEFAULT '',
    salary_text TEXT DEFAULT '',              -- raw posted salary if any
    job_type TEXT DEFAULT 'job',              -- job | signal | lead
    posted_at TEXT,                           -- ISO date from source (nullable)
    first_seen_at TEXT NOT NULL,              -- ISO datetime we discovered it
    -- classification (rules)
    -- ANYWHERE | EMEA_OK | EGYPT_LISTED | RELOCATION | HYBRID_ONSITE | EXCLUDED | UNKNOWN
    location_tag TEXT DEFAULT 'UNKNOWN',
    automation_class TEXT DEFAULT 'unknown',  -- none|plus|open_to_learn|required
    apply_by_email INTEGER DEFAULT 0,
    -- scoring
    rule_score REAL DEFAULT 0,
    llm_score REAL,
    tier TEXT DEFAULT 'C',                    -- A | B | C
    llm_reasoning TEXT DEFAULT '',
    salary_estimate TEXT DEFAULT '',          -- JSON {min,max,currency,type,confidence}
    -- pipeline state
    status TEXT DEFAULT 'new',                -- new|viewed|applied|interview|offer|rejected|hidden
    notified_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_jobs_tier ON jobs(tier);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);

CREATE TABLE IF NOT EXISTS dorks (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    family TEXT NOT NULL,
    fitness REAL DEFAULT 0,
    generation INTEGER DEFAULT 0,
    parent_ids TEXT DEFAULT '',               -- JSON array
    runs INTEGER DEFAULT 0,
    total_results INTEGER DEFAULT 0,
    valid_results INTEGER DEFAULT 0,
    retired INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    source TEXT DEFAULT 'all',
    jobs_found INTEGER DEFAULT 0,
    jobs_new INTEGER DEFAULT 0,
    error TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    ats TEXT,
    board_token TEXT,
    enabled INTEGER DEFAULT 1,
    blacklisted INTEGER DEFAULT 0,
    jobs_found INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS drafts (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    draft_json TEXT NOT NULL,
    model TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")  # scheduler + scan threads share the DB
    return conn


# ------------------------------------------------------- the live-pool "bar"
# "The reachability bar": a job earns a slot in the visible pool only when we can
# verify it is actually reachable from Egypt — a compatible location tag, or a
# posting with a real job description (so the LLM stage can refine the tag).
# Location-less stubs (US-centric boards that scrape location='nan') stay out.
POOL_WORTHY_TAGS = ("ANYWHERE", "EMEA_OK", "EGYPT_LISTED", "RELOCATION")
MIN_DESCRIPTION_LEN = 300
_TAGS_SQL = ", ".join(f"'{t}'" for t in POOL_WORTHY_TAGS)
# single source of truth for the SQL + Python form of the rule
POOL_WORTHY_SQL = (
    f"(COALESCE(location_tag,'') IN ({_TAGS_SQL})"
    f" OR length(trim(coalesce(description,''))) >= {MIN_DESCRIPTION_LEN})"
)


def pool_worthy(row: sqlite3.Row) -> bool:
    """POOL_WORTHY_SQL for a row already in hand (purge/rescore paths)."""
    keys = row.keys()
    tag = (row["location_tag"] if "location_tag" in keys else "") or ""
    desc = (row["description"] or "") if "description" in keys else ""
    return tag.upper() in POOL_WORTHY_TAGS or len(desc.strip()) >= MIN_DESCRIPTION_LEN


# Rows eligible for the instant A-tier Discord alert — the live pool only, so a
# job the same scan excluded can never ping Discord.
ALERT_TARGET_SQL = (
    "tier='A' AND notified_at IS NULL AND status != 'hidden'"
    " AND COALESCE(location_tag,'') != 'EXCLUDED' AND " + POOL_WORTHY_SQL
)


MIGRATIONS = [
    # v0.2.0 — employment/timezone metadata + company discovery flag
    "ALTER TABLE jobs ADD COLUMN employment_type TEXT DEFAULT 'full_time'",
    "ALTER TABLE jobs ADD COLUMN timezone_fit TEXT DEFAULT 'unknown'",
    "ALTER TABLE companies ADD COLUMN discovered INTEGER DEFAULT 0",
    "ALTER TABLE companies ADD COLUMN careers_url TEXT DEFAULT ''",
    # v0.3.1 — ghost-job detection
    "ALTER TABLE jobs ADD COLUMN ghost_score INTEGER DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN ghost_signals TEXT DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN times_seen INTEGER DEFAULT 1",
    "ALTER TABLE jobs ADD COLUMN last_seen_at TEXT",
]


def init_db(db_path: str | Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            with contextlib.suppress(sqlite3.OperationalError):  # column already exists
                conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def weekly_kept_by_week(conn: sqlite3.Connection, weeks: int = 8) -> list[dict]:
    """Kept-jobs-per-week trend — the company-snowball metric.

    "Kept" = `status != 'hidden'` (the live-pool definition), bucketed by the
    ISO week of `first_seen_at`, oldest first, with empty weeks zero-filled so
    the wall is continuous. If the maintained company set is doing its job, the
    `kept` bar grows even when absolute discovery (`seen`) is flat.

    The window is anchored in UTC — the same frame rows are stamped in — so a
    job can't land one ISO week off. Buckets are live cohorts, not frozen
    snapshots: hiding a job later shrinks its week's `kept` retroactively."""
    today = datetime.now(UTC).date()
    this_monday = today - timedelta(days=today.isoweekday() - 1)  # Monday

    buckets: dict[tuple[int, int], dict[str, int]] = {}
    order: list[tuple[int, int]] = []
    for i in range(weeks - 1, -1, -1):  # oldest → newest
        monday = this_monday - timedelta(weeks=i)
        iso = monday.isocalendar()
        key = (iso[0], iso[1])
        buckets[key] = {"kept": 0, "a": 0, "seen": 0}
        order.append(key)

    for r in conn.execute("SELECT first_seen_at, status, tier FROM jobs"):
        raw = (r["first_seen_at"] or "")[:10]  # YYYY-MM-DD (tz/format-agnostic)
        try:
            iso = date.fromisoformat(raw).isocalendar()
        except ValueError:
            continue
        b = buckets.get((iso[0], iso[1]))
        if b is None:  # outside the window
            continue
        b["seen"] += 1
        if r["status"] != "hidden":
            b["kept"] += 1
            if r["tier"] == "A":
                b["a"] += 1

    return [{"week": f"{y}-W{w:02d}", **buckets[(y, w)]} for (y, w) in order]
