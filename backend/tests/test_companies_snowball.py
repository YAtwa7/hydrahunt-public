"""Tests for company registry sync, discovery, and auto-promotion."""

from unittest.mock import patch

from app.db import connect, init_db
from app.pipeline.companies import auto_promote, discover, sync_from_yaml

CFG = {
    "companies": [
        {"name": "GitLab", "ats": "greenhouse", "board_token": "gitlab", "enabled": True},
    ],
    "blacklist": ["Testlio"],
}


def _db(tmp_path):
    p = tmp_path / "t.db"
    init_db(p)
    return connect(p)


def test_sync_upserts_and_blacklists(tmp_path):
    conn = _db(tmp_path)
    sync_from_yaml(conn, CFG)
    row = conn.execute("SELECT * FROM companies WHERE name='GitLab'").fetchone()
    assert row["ats"] == "greenhouse" and row["enabled"] == 1
    row = conn.execute("SELECT * FROM companies WHERE name='Testlio'").fetchone()
    assert row["blacklisted"] == 1
    conn.close()


def test_discover_adds_watchlist_once(tmp_path):
    conn = _db(tmp_path)
    discover(conn, "NewCo")
    discover(conn, "NewCo")  # idempotent
    rows = conn.execute("SELECT * FROM companies WHERE name='NewCo'").fetchall()
    assert len(rows) == 1 and rows[0]["discovered"] == 1 and rows[0]["enabled"] == 0
    conn.close()


def test_auto_promote_enables_detected(tmp_path):
    conn = _db(tmp_path)
    discover(conn, "NewCo")
    with patch("app.pipeline.companies.detect_ats", return_value=("greenhouse", "newco")):
        promoted = auto_promote(conn)
    assert promoted == 1
    row = conn.execute("SELECT * FROM companies WHERE name='NewCo'").fetchone()
    assert row["enabled"] == 1 and row["ats"] == "greenhouse"
    conn.close()


def test_auto_promote_marks_unprobed_as_none(tmp_path):
    conn = _db(tmp_path)
    discover(conn, "NoAtsCo")
    with patch("app.pipeline.companies.detect_ats", return_value=None):
        promoted = auto_promote(conn)
    assert promoted == 0
    row = conn.execute("SELECT * FROM companies WHERE name='NoAtsCo'").fetchone()
    assert row["ats"] == "none" and row["enabled"] == 0
    conn.close()


def test_auto_promote_never_touches_blacklisted(tmp_path):
    conn = _db(tmp_path)
    discover(conn, "Testlio")
    conn.execute("UPDATE companies SET blacklisted=1 WHERE name='Testlio'")
    conn.commit()
    with patch("app.pipeline.companies.detect_ats", return_value=("lever", "testlio")):
        promoted = auto_promote(conn)
    assert promoted == 0
    conn.close()
