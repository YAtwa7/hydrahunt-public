"""Re-apply the current hard filters + location tagging to every stored job.

Corrects rows that were inserted under older filter logic (or re-tagged by the
LLM) and hides anything that no longer belongs in the live pool: EXCLUDED
regions (gupy/quickin/LATAM, non-English postings), blocklisted titles
(regulatory/compliance/manufacturing/automation...), US-only employers, etc.

Idempotent. User-driven rows (applied/interview/offer/rejected) are never
auto-hidden. Tags are only ever tightened to EXCLUDED, never relaxed.

Usage (from repo root):
    .venv\\Scripts\\python.exe scripts\\rescore_pool.py --dry-run
    .venv\\Scripts\\python.exe scripts\\rescore_pool.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.collectors.base import RawJob  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import connect, pool_worthy  # noqa: E402
from app.pipeline.filters import HardFilter  # noqa: E402
from app.pipeline.location import tag_location  # noqa: E402

# Statuses the user drives by hand — never flip these to hidden.
_IMMUTABLE = {"applied", "interview", "offer", "rejected"}


def _row_to_raw(row) -> RawJob:
    return RawJob(
        source=row["source"] or "",
        source_url=row["source_url"] or "",
        company=row["company"] or "",
        title=row["title"] or "",
        location=row["location"] or "",
        description=row["description"] or "",
        salary_text=row["salary_text"] or "",
        job_type=row["job_type"] or "job",
        posted_at=row["posted_at"],
    )


def _why_hidden(row, tag: str) -> str:
    """Human-readable reason this row is below the live-pool bar."""
    if tag == "EXCLUDED":
        return "EXCLUDED location"
    if not pool_worthy(row):
        stored = (row["location_tag"] or "").upper()
        if stored == "UNKNOWN":
            return "unknown location + no real job description"
        return f"location tag {stored} not compatible"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    cfg = load_config()
    hf = HardFilter(cfg)
    conn = connect(cfg.db_path)
    try:
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        hid: list[tuple[int, str, str, str]] = []
        retagged = 0
        for row in rows:
            raw = _row_to_raw(row)
            reason = hf.reject_reason(raw)
            tag = tag_location(raw, cfg.excluded_regions)

            # only ever tighten the tag to EXCLUDED — never relax it
            if tag == "EXCLUDED" and row["location_tag"] != "EXCLUDED":
                retagged += 1
                if not args.dry_run:
                    conn.execute("UPDATE jobs SET location_tag='EXCLUDED' WHERE id=?", (row["id"],))
                tag = "EXCLUDED"

            below_bar = tag == "EXCLUDED" or row["location_tag"] == "EXCLUDED"
            if below_bar and row["status"] not in _IMMUTABLE:
                if not args.dry_run and row["location_tag"] != "EXCLUDED":
                    conn.execute(
                        "UPDATE jobs SET location_tag='EXCLUDED' WHERE id=?", (row["id"],)
                    )
            elif not below_bar and not pool_worthy(row):
                below_bar = True

            if below_bar and row["status"] not in _IMMUTABLE and row["status"] != "hidden":
                hid.append((row["id"], row["title"], row["company"], reason or _why_hidden(row, tag)))
                if not args.dry_run:
                    conn.execute("UPDATE jobs SET status='hidden' WHERE id=?", (row["id"],))

        if not args.dry_run:
            conn.commit()

        visible_before = sum(1 for r in rows if r["status"] != "hidden")
        print(f"scanned {len(rows)} rows · re-tagged EXCLUDED {retagged} · hid {len(hid)}")
        print(f"visible: {visible_before} -> {visible_before - len(hid)}")
        for jid, title, company, why in hid:
            print(f"  hid #{jid} {title} @ {company} — {why}")
        if args.dry_run:
            print("(dry run — nothing written)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
