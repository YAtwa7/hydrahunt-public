"""Company registry — dynamic, not static.

- companies.yml seeds the DB on every scan (YAML = user-curated source of truth)
- New companies discovered from leads/signals land in the watchlist
  (enabled=0) for user review on the dashboard Companies page
- Enabling a watchlist company triggers ATS auto-detection
  (greenhouse/lever/ashby probed by slug) + careers page guess
"""

from __future__ import annotations

import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx

log = logging.getLogger("hydrahunt.companies")

ATS_PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    "breezy": "https://{slug}.breezy.hr/json",
    "recruitee": "https://{slug}.recruitee.com/api/offers",
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def sync_from_yaml(conn: sqlite3.Connection, companies_cfg: dict) -> None:
    """Upsert YAML companies into DB. YAML edits win on ats/token/enabled."""
    blacklist = {c.lower() for c in companies_cfg.get("blacklist", [])}
    for c in companies_cfg.get("companies", []):
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        conn.execute(
            """INSERT INTO companies (name, ats, board_token, enabled, blacklisted)
               VALUES (?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 ats=excluded.ats, board_token=excluded.board_token,
                 enabled=excluded.enabled""",
            (name, c.get("ats", ""), c.get("board_token", ""),
             int(c.get("enabled", True)), int(name.lower() in blacklist)),
        )
    for name in companies_cfg.get("blacklist", []):
        # ensure blacklisted companies exist in the registry (visible on dashboard)
        conn.execute(
            """INSERT INTO companies (name, enabled, blacklisted) VALUES (?, 0, 1)
               ON CONFLICT(name) DO UPDATE SET blacklisted=1, enabled=0""",
            (name,),
        )
    conn.commit()


def discover(conn: sqlite3.Connection, company: str) -> None:
    """Add an unknown company to the watchlist (no-op if known/blacklisted)."""
    name = company.strip()
    if not name or len(name) < 2:
        return
    exists = conn.execute("SELECT 1 FROM companies WHERE lower(name)=?", (name.lower(),)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO companies (name, enabled, discovered) VALUES (?, 0, 1)", (name,)
        )
        log.info("watchlist: discovered %s", name)


def auto_promote(conn: sqlite3.Connection, max_probes: int = 5) -> int:
    """Snowball: probe watchlist companies for a public ATS; if found, they
    become actively hunted — the company list grows itself every scan.

    Bounded probes per scan so ATS detection never dominates scan time.
    """
    rows = conn.execute(
        "SELECT id, name FROM companies WHERE discovered=1 AND enabled=0 "
        "AND blacklisted=0 AND (ats IS NULL OR ats='') LIMIT ?",
        (max_probes,),
    ).fetchall()
    promoted = 0
    for row in rows:
        detected = detect_ats(row["name"])
        if detected:
            conn.execute(
                "UPDATE companies SET ats=?, board_token=?, enabled=1 WHERE id=?",
                (detected[0], detected[1], row["id"]),
            )
            log.info("auto-promoted %s → actively hunted (%s/%s)", row["name"], *detected)
            promoted += 1
        else:
            # mark probed so we don't retry every scan (ats='none')
            conn.execute("UPDATE companies SET ats='none' WHERE id=?", (row["id"],))
    conn.commit()
    return promoted


def detect_ats(name: str) -> tuple[str, str] | None:
    """Probe public ATS APIs by company slug (concurrent, bounded). Returns (ats, token)."""
    slug = slugify(name)
    if not slug:
        return None

    def probe(item: tuple[str, str]) -> tuple[str, str] | None:
        ats, url_tpl = item
        try:
            resp = httpx.get(url_tpl.format(slug=slug), timeout=5.0)
            return (ats, slug) if resp.status_code == 200 else None
        except httpx.HTTPError:
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(probe, ATS_PROBES.items()):
            if result:
                return result
    return None
