"""In-process scheduler — cheap sources sweep often, heavy scrapers stay polite.

Runs inside the FastAPI process via APScheduler BackgroundScheduler.
Times come from config.yml `scheduler` section. The Windows boot part
is handled by scripts/install_autostart.ps1 (Task Scheduler at logon).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import AppConfig
from app.db import connect

log = logging.getLogger("hydrahunt.scheduler")

_scheduler: BackgroundScheduler | None = None


FAST_SOURCES = ["remote_boards", "ats_companies", "hackernews", "rocketship"]


def _fast_sweep(cfg: AppConfig) -> None:
    """Cheap sources only (APIs/RSS/sitemaps) — safe to run every 30 min."""
    from app import scancontrol

    if not scancontrol.try_start_scan(sources=FAST_SOURCES, digest=False):
        log.info("fast sweep skipped — another scan is running")


def _deep_scan(cfg: AppConfig) -> None:
    """Full scan incl. JobSpy boards + DorkLab."""
    from app import scancontrol

    log.info("scheduled deep scan starting")
    if not scancontrol.try_start_scan():
        log.info("a scan is already running — skipping scheduled run")


def _daily_digest(cfg: AppConfig) -> None:
    """Digest window — sends ONLY if something new arrived in the last 6h
    (skip-if-empty: multiple windows a day, zero spam)."""
    from datetime import timedelta

    from app.db import POOL_WORTHY_SQL, connect
    from app.notify import discord

    conn = connect(cfg.db_path)
    try:
        since = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) new, SUM(tier='A') a, SUM(tier='B') b FROM jobs WHERE first_seen_at > ?",
            (since,),
        ).fetchone()
        total = conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status != 'hidden' AND " + POOL_WORTHY_SQL
        ).fetchone()["c"]
    finally:
        conn.close()
    if not (row["new"] or 0):
        log.info("digest window: nothing new in 6h — skipping")
        return
    stats = {
        "jobs_new": row["new"] or 0,
        "tier_a": row["a"] or 0,
        "tier_b": row["b"] or 0,
        "jobs_found": total,
    }
    discord.send_digest(cfg.settings.discord_webhook_url, stats)


def _weekly_evolution(cfg: AppConfig) -> None:
    from app.dorklab.evolve import evolve

    log.info("weekly dork evolution starting")
    conn = connect(cfg.db_path)
    try:
        result = evolve(conn, cfg)
        log.info("evolution done: %s", result)
    except Exception:
        log.exception("evolution crashed")
    finally:
        conn.close()


def start(cfg: AppConfig) -> BackgroundScheduler | None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    sc = cfg.raw.get("scheduler", {})
    if not sc.get("enabled", True):
        log.info("scheduler disabled in config")
        return None

    _scheduler = BackgroundScheduler(daemon=True)

    # fast sweep: cheap sources every N minutes
    interval = int(sc.get("fast_interval_minutes", 30))
    _scheduler.add_job(
        _fast_sweep,
        CronTrigger(minute=f"*/{interval}"),
        args=[cfg],
        id="fast_sweep",
        name=f"Fast sweep (every {interval}m)",
        replace_existing=True,
    )

    # deep scans: full pipeline every N hours (heavy surfaces stay polite)
    deep_h = int(sc.get("deep_interval_hours", 3))
    _scheduler.add_job(
        _deep_scan,
        CronTrigger(hour=f"*/{deep_h}"),
        args=[cfg],
        id="deep_scan",
        name=f"Deep scan (every {deep_h}h)",
        replace_existing=True,
    )

    # digest windows (skip-if-empty)
    for i, t in enumerate(sc.get("digest_times", ["09:00"])):
        dh, dm = map(int, t.split(":"))
        _scheduler.add_job(
            _daily_digest,
            CronTrigger(hour=dh, minute=dm),
            args=[cfg],
            id=f"digest_{i}",
            name=f"Digest {t}",
            replace_existing=True,
        )

    # dorklab evolution: daily
    eh, em = map(int, sc.get("dorklab_evolution_time", "10:00").split(":"))
    _scheduler.add_job(
        _weekly_evolution,
        CronTrigger(hour=eh, minute=em),
        args=[cfg],
        id="dorklab_evolution",
        name="DorkLab daily evolution",
        replace_existing=True,
    )

    _scheduler.start()
    log.info(
        "scheduler started — fast sweep every %dm, deep every %dh, digests %s, evolution %s",
        interval, deep_h, sc.get("digest_times"), sc.get("dorklab_evolution_time", "10:00"),
    )
    return _scheduler


def next_runs() -> list[dict]:
    if not _scheduler or not _scheduler.running:
        return []
    return [
        {"id": j.id, "name": j.name, "next_run": j.next_run_time.isoformat() if j.next_run_time else None}
        for j in _scheduler.get_jobs()
    ]
