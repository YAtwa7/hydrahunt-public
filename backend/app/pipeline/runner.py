"""Scan orchestrator — the full pipeline, end to end.

collect → filter → dedup-insert → tag/classify → rules-score
→ LLM-score top N → salary estimate → Discord alerts
Every stage records into the scans table for source-health metrics.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from app import progress
from app.collectors import (
    ats,
    dorklab_collector,
    hackernews,
    jobspy_collector,
    reddit,
    remote_boards,
    rocketship,
)
from app.collectors.base import RawJob
from app.config import AppConfig, load_config
from app.db import ALERT_TARGET_SQL, POOL_WORTHY_SQL, connect, init_db
from app.notify import discord
from app.pipeline.companies import auto_promote, discover, sync_from_yaml
from app.pipeline.employment import detect_employment_type, detect_timezone_fit
from app.pipeline.filters import HardFilter
from app.pipeline.ghost import compute_ghost_score, tier_penalty
from app.pipeline.location import tag_location
from app.pipeline.rules_score import classify_automation, detect_email_apply, score_job, tier_from_score
from app.pipeline.salary import estimate
from app.scoring.llm import score_with_llm

log = logging.getLogger("jobhunt.runner")

COLLECTORS = {
    "jobspy": lambda cfg: jobspy_collector.collect(cfg.raw),
    "remote_boards": lambda cfg: remote_boards.collect(cfg.raw),
    "ats_companies": lambda cfg: ats.collect(cfg.raw, cfg.companies),
    "reddit": lambda cfg: reddit.collect(cfg.raw, cfg.settings),
    "hackernews": lambda cfg: hackernews.collect(cfg.raw),
    "dorklab": lambda cfg: dorklab_collector.collect(cfg),
    "rocketship": lambda cfg: rocketship.collect(cfg),
}


def _insert_job(conn: sqlite3.Connection, job: RawJob, scored: dict) -> bool:
    """Insert if new; on dedup hit, bump times_seen + last_seen_at (repost tracking)."""
    now = datetime.now(UTC).isoformat()
    try:
        conn.execute(
            """INSERT INTO jobs (dedup_hash, source, source_url, company, title, location,
                                 description, salary_text, job_type, posted_at, first_seen_at,
                                 location_tag, automation_class, apply_by_email,
                                 rule_score, llm_score, tier, llm_reasoning, salary_estimate,
                                 employment_type, timezone_fit, ghost_score, ghost_signals,
                                 last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job.dedup_hash,
                job.source,
                job.source_url,
                job.company,
                job.title,
                job.location,
                job.description,
                job.salary_text,
                job.job_type,
                job.posted_at,
                now,
                scored["location_tag"],
                scored["automation_class"],
                int(scored["apply_by_email"]),
                scored["rule_score"],
                scored["llm_score"],
                scored["tier"],
                scored["llm_reasoning"],
                scored["salary_estimate"],
                scored["employment_type"],
                scored["timezone_fit"],
                scored["ghost_score"],
                scored["ghost_signals"],
                now,
            ),
        )
        return True
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE jobs SET times_seen=times_seen+1, last_seen_at=? WHERE dedup_hash=?",
            (now, job.dedup_hash),
        )
        return False


def _score_job(job: RawJob, cfg: AppConfig, times_seen: int = 1) -> dict:
    """Rules-only scoring. LLM refinement happens separately (concurrent stage)."""
    location_tag = tag_location(job, cfg.excluded_regions)
    automation_class = classify_automation(job)
    rule_score = score_job(job, cfg, location_tag, automation_class)

    ghost_score, ghost_signals = compute_ghost_score(
        posted_at=job.posted_at,
        times_seen=times_seen,
        description=job.description,
        salary_text=job.salary_text,
        location=job.location,
    )
    rule_score = round(max(0.0, rule_score - tier_penalty(ghost_score)), 1)

    llm_score, llm_reasoning = None, ""  # LLM fills these in the concurrent stage
    employment_type = detect_employment_type(job)
    sal = estimate(job, location_tag, cfg.profile, employment_type)
    return {
        "location_tag": location_tag,
        "automation_class": automation_class,
        "apply_by_email": detect_email_apply(job),
        "rule_score": rule_score,
        "llm_score": llm_score,
        "tier": tier_from_score(rule_score, llm_score),
        "llm_reasoning": llm_reasoning,
        "salary_estimate": sal["display"],
        "employment_type": employment_type,
        "timezone_fit": detect_timezone_fit(job),
        "ghost_score": ghost_score,
        "ghost_signals": "; ".join(ghost_signals),
    }


def run_scan(
    cfg: AppConfig | None = None,
    sources: list[str] | None = None,
    digest: bool = True,
    stop: threading.Event | None = None,
) -> dict:
    cfg = cfg or load_config()
    db_path = cfg.db_path
    init_db(db_path)  # idempotent; runner must not depend on main() having run
    conn = connect(db_path)
    started = datetime.now(UTC).isoformat()
    scan_id = conn.execute(
        "INSERT INTO scans (started_at) VALUES (?)", (started,)
    ).lastrowid

    stats: dict = {"jobs_found": 0, "jobs_new": 0, "tier_a": 0, "tier_b": 0, "errors": []}
    hf = HardFilter(cfg)
    max_llm = int(cfg.raw.get("llm", {}).get("max_jobs_per_scan", 20))
    sync_from_yaml(conn, cfg.companies)  # YAML → DB registry
    promoted = auto_promote(conn)  # watchlist → actively hunted (snowball)
    if promoted:
        log.info("company snowball: %d watchlist companies auto-promoted", promoted)

    preempted = False
    try:
        # 1. collect (collectors run concurrently; one crashing never blocks others)
        raw_jobs: list[RawJob] = []
        active = {n: fn for n, fn in COLLECTORS.items() if not sources or n in sources}
        progress.reset(list(active))
        pool = ThreadPoolExecutor(max_workers=len(active) or 1)
        futures = {pool.submit(fn, cfg): name for name, fn in active.items()}
        try:
            for future in as_completed(futures):
                name = futures[future]
                try:
                    found = future.result()
                    raw_jobs.extend(found)
                    progress.collector_done(name, len(found))
                except Exception as exc:  # collectors shouldn't raise, but belt+braces
                    log.exception("collector %s crashed", name)
                    progress.collector_failed(name)
                    stats["errors"].append(f"{name}: {exc}")
        finally:
            # A collector already running cannot be interrupted; cancel_futures
            # drops the ones still queued so a preempted sweep unwinds promptly
            # instead of blocking the deep scan behind the full collect tail.
            if stop is not None and stop.is_set():
                preempted = True
                log.info("scan interrupted — a deep scan is taking over")
                pool.shutdown(wait=False, cancel_futures=True)
            else:
                pool.shutdown(wait=True)
        stats["jobs_found"] = len(raw_jobs)

        if preempted:
            # Stop here: filtering/scoring/LLM/notify on a truncated harvest would
            # burn tokens the deep scan is about to redo, and fire Discord alerts
            # for a scan we just declared interrupted.
            progress.set_phase("done")
            log.info("preempted scan skipped filter/score/LLM/notify")
            return {
                **stats,
                "preempted": True,
                "errors": [*stats["errors"], "preempted by a deep scan"],
            }

        # 2. filter
        progress.set_phase("filtering")
        passed = [j for j in raw_jobs if hf.reject_reason(j) is None]
        progress.set_filtered(len(passed))
        log.info("collected=%d passed_filters=%d", len(raw_jobs), len(passed))

        # 3. rules-score everything, shortlist for LLM
        prescore = []
        for job in passed:
            tag = tag_location(job, cfg.excluded_regions)
            if tag == "EXCLUDED":
                continue
            prescore.append((score_job(job, cfg, tag, classify_automation(job)), job))
        prescore.sort(key=lambda t: t[0], reverse=True)
        llm_shortlist = {id(j) for _, j in prescore[:max_llm]}
        progress.set_llm_total(len(llm_shortlist))

        # 4. rules-score + insert everything (fast, zero tokens)
        progress.set_phase("scoring")
        rule_scores: dict[str, float] = {}
        shortlist_jobs: list[RawJob] = []
        new_hashes: list[str] = []
        for i, (_, job) in enumerate(prescore):
            scored = _score_job(job, cfg)
            rule_scores[job.dedup_hash] = scored["rule_score"]
            if id(job) in llm_shortlist:
                shortlist_jobs.append(job)
            if _insert_job(conn, job, scored):
                stats["jobs_new"] += 1
                new_hashes.append(job.dedup_hash)
            if job.company:
                discover(conn, job.company)
            if i % 25 == 0:
                progress.set_scored(i)
        progress.set_scored(len(prescore))
        conn.commit()

        # 5. LLM-score the shortlist CONCURRENTLY (fetch-only workers,
        #    main thread writes — sqlite connections can't cross threads)
        def _llm_fetch(job: RawJob) -> tuple[str, dict | None]:
            try:
                return job.dedup_hash, score_with_llm(job, cfg)
            except Exception:  # noqa: BLE001 — LLM stage must never kill a scan
                log.warning("LLM scoring crashed for %r — rules stand", job.title, exc_info=True)
                return job.dedup_hash, None

        valid_tags = {
            "ANYWHERE", "EMEA_OK", "EGYPT_LISTED",
            "RELOCATION", "HYBRID_ONSITE", "EXCLUDED", "UNKNOWN",
        }
        with ThreadPoolExecutor(max_workers=3) as pool:
            futs: list = [pool.submit(_llm_fetch, j) for j in shortlist_jobs]
            for f in as_completed(futs):
                _res: Any = f.result()
                dedup_hash, verdict = _res
                progress.inc_llm_done()
                if not verdict:
                    continue
                llm_score = float(verdict["score"])
                llm_tag: str | None = verdict.get("location_fit")
                new_tier = tier_from_score(rule_scores.get(dedup_hash, 0), llm_score)
                conn.execute(
                    """UPDATE jobs SET llm_score=?, tier=?, llm_reasoning=?,
                       automation_class=COALESCE(?, automation_class),
                       location_tag=COALESCE(?, location_tag)
                       WHERE dedup_hash=?""",
                    (
                        llm_score,
                        new_tier,
                        str(verdict.get("reasoning", "")),
                        verdict.get("automation")
                        if verdict.get("automation") in ("none", "plus", "open_to_learn", "required")
                        else None,
                        llm_tag if llm_tag in valid_tags else None,
                        dedup_hash,
                    ),
                )
                # tier counts finalized from the DB below (single source of truth)
        conn.commit()

        # An EXCLUDED location is a hard no. Rules refuse EXCLUDED at insert, but
        # the LLM stage above may refine location_fit to EXCLUDED — so hide any
        # such job that is still sitting in the live pool (not applied/interview).
        conn.execute(
            "UPDATE jobs SET status='hidden' "
            "WHERE location_tag='EXCLUDED' AND status IN ('new','viewed')"
        )
        conn.commit()

        # final tier counts from the DB (rules-tier + LLM upgrades, no double-count).
        # Counts reflect the live pool only: an EXCLUDED/suppressed row never
        # inflates the digest or the "A-tier found" claim.
        if new_hashes:
            placeholders = ",".join("?" * len(new_hashes))
            for row in conn.execute(
                f"SELECT tier, COUNT(*) c FROM jobs WHERE dedup_hash IN ({placeholders})"
                " AND status != 'hidden' AND COALESCE(location_tag,'') != 'EXCLUDED'"
                f" AND {POOL_WORTHY_SQL} GROUP BY tier",
                new_hashes,
            ):
                if row["tier"] == "A":
                    stats["tier_a"] = row["c"]
                elif row["tier"] == "B":
                    stats["tier_b"] = row["c"]

        # 5. notify A-tier instantly — only jobs actually in the live pool (a
        # job excluded during this run must never ping Discord).
        progress.set_phase("notifying")
        if stats["tier_a"]:
            now = datetime.now(UTC).isoformat()
            rows = conn.execute(
                f"SELECT * FROM jobs WHERE {ALERT_TARGET_SQL} ORDER BY rule_score DESC"
            ).fetchall()
            sent = discord.notify_new_jobs(cfg.settings.discord_webhook_url, rows)
            # only stamp what was confirmed sent: an unconfigured webhook or a
            # swallowed HTTP failure must not silence these rows forever
            if sent >= len(rows):
                conn.execute(f"UPDATE jobs SET notified_at=? WHERE {ALERT_TARGET_SQL}", (now,))
            conn.commit()
            log.info("discord: %d A-tier alerts sent", sent)

        if digest:
            discord.send_digest(cfg.settings.discord_webhook_url, stats)
        progress.set_phase("done")
        conn.execute(
            "UPDATE scans SET finished_at=?, jobs_found=?, jobs_new=?, error=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                stats["jobs_found"],
                stats["jobs_new"],
                "; ".join(stats["errors"]),
                scan_id,
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — record the crash before it bubbles up
        log.exception("scan %s crashed", scan_id)
        progress.set_phase("crashed")
        conn.execute(
            "UPDATE scans SET finished_at=?, jobs_found=?, jobs_new=?, error=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                stats["jobs_found"],
                stats["jobs_new"],
                f"CRASHED: {exc}",
                scan_id,
            ),
        )
        conn.commit()
        raise
    finally:
        # A preempted scan returns early from inside the try, so make sure its
        # row is still closed out — an unfinished row is what the reaper treats
        # as an orphan and what "last scan" used to pick up.
        if preempted:
            try:
                conn.execute(
                    "UPDATE scans SET finished_at=?, jobs_found=?, jobs_new=?, error=? WHERE id=?",
                    (
                        datetime.now(UTC).isoformat(),
                        stats["jobs_found"],
                        stats["jobs_new"],
                        "; ".join(stats["errors"]),
                        scan_id,
                    ),
                )
                conn.commit()
            except sqlite3.Error:
                log.warning("could not close out preempted scan row %s", scan_id, exc_info=True)
        conn.close()
    return stats
