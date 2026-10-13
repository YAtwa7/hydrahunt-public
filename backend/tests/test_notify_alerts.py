"""Regression tests for the instant A-tier Discord alert (scan step 5).

Pins two user-visible failure modes:
- rows the same scan excluded (EXCLUDED, hidden, below the pool bar) must never
  be alerted;
- `notified_at` may only be stamped when the alerts actually went out — an
  unconfigured webhook must not silence those rows forever.
"""

from app.db import ALERT_TARGET_SQL, connect, init_db


def _insert(db, *, loc_tag="ANYWHERE", desc="Manual QA. " * 80, tier="A",
            status="new", notified=False, dedup="h"):
    conn = connect(db)
    conn.execute(
        "INSERT INTO jobs (dedup_hash, source, source_url, company, title, "
        "first_seen_at, location_tag, description, tier, status, notified_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (dedup, "t", "u", "c", "QA Engineer", "2026-01-01", loc_tag, desc, tier,
         status, "2026-01-01T00:00:00Z" if notified else None),
    )
    conn.commit()
    conn.close()


def test_alert_targets_are_the_live_pool_only(tmp_path):
    db = tmp_path / "a.db"
    init_db(db)
    _insert(db, loc_tag="EXCLUDED", dedup="excluded")
    _insert(db, loc_tag="UNKNOWN", desc="Manual QA.", dedup="thin")  # below the bar
    _insert(db, loc_tag="ANYWHERE", dedup="good")
    _insert(db, loc_tag="ANYWHERE", status="hidden", dedup="hidden")
    _insert(db, loc_tag="EMEA_OK", dedup="already", notified=True)
    _insert(db, loc_tag="ANYWHERE", tier="B", dedup="b-tier")

    conn = connect(db)
    rows = conn.execute(f"SELECT dedup_hash FROM jobs WHERE {ALERT_TARGET_SQL}").fetchall()
    conn.close()
    assert [r["dedup_hash"] for r in rows] == ["good"]


def test_already_notified_is_not_re_alerted(tmp_path):
    db = tmp_path / "b.db"
    init_db(db)
    _insert(db, dedup="sent", notified=True)
    conn = connect(db)
    assert conn.execute(f"SELECT COUNT(*) c FROM jobs WHERE {ALERT_TARGET_SQL}").fetchone()["c"] == 0
    conn.close()


def test_notified_at_only_stamped_when_alerts_were_sent(tmp_path):
    """The runner stamps rows only if `sent >= len(rows)`; an unconfigured
    webhook (sent == 0) must leave every row unnotified so it stays retryable."""
    db = tmp_path / "c.db"
    init_db(db)
    _insert(db, dedup="x")
    _insert(db, dedup="y", loc_tag="EMEA_OK")

    conn = connect(db)
    rows = conn.execute(f"SELECT * FROM jobs WHERE {ALERT_TARGET_SQL}").fetchall()
    assert len(rows) == 2

    # --- simulate the runner's guard with nothing actually sent
    sent, now = 0, "2026-01-01T00:00:00Z"
    if sent >= len(rows):
        conn.execute(f"UPDATE jobs SET notified_at=? WHERE {ALERT_TARGET_SQL}", (now,))
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) c FROM jobs WHERE notified_at IS NULL AND tier='A'"
    ).fetchone()["c"] == 2, "rows must stay retryable when nothing was sent"

    # --- and with a webhook that confirms every alert
    sent = len(rows)
    if sent >= len(rows):
        conn.execute(f"UPDATE jobs SET notified_at=? WHERE {ALERT_TARGET_SQL}", (now,))
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) c FROM jobs WHERE notified_at IS NULL AND tier='A'"
    ).fetchone()["c"] == 0
    conn.close()
