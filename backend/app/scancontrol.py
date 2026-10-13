"""Shared scan control — single-flight lock + state, used by the API
endpoint AND the scheduler so manual and scheduled scans never overlap.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

log = logging.getLogger("hydrahunt.scancontrol")

_lock = threading.Lock()
state: dict = {"running": False, "started_at": None, "result": None, "error": None}

# A full (deep) scan supersedes a running fast sweep: the deep scan is a
# superset of the fast sources, so letting it win beats skipping it and
# starving JobSpy (the biggest job source) for another cycle.
#
# The sweep is told to stop via this event and releases the lock when it
# unwinds. Crucially this NEVER blocks the caller: the Discord bot invokes
# try_start_scan() from inside an async handler running on the event loop, so
# waiting here would freeze slash commands and gateway heartbeats.
_stop = threading.Event()


def _worker(sources: list[str] | None, digest: bool) -> None:
    from app.pipeline.runner import run_scan

    _stop.clear()
    try:
        state["result"] = run_scan(sources=sources, digest=digest, stop=_stop)
    except Exception as exc:  # noqa: BLE001 — surface to UI, never die silently
        log.exception("scan worker crashed")
        state["error"] = str(exc)
    finally:
        state["running"] = False
        _lock.release()
        log.info("scan lock released")
        _drain_pending()


def try_start_scan(sources: list[str] | None = None, digest: bool = True) -> bool:
    """Start a background scan if none is running. Returns True if started.

    Never blocks. A deep scan (sources=None) may preempt a running fast sweep:
    it signals the sweep to stop and returns False; the sweep unwinds and
    releases the lock, and the queued deep scan starts at that point (see
    _pending). A fast sweep never preempts anything, and a second deep request
    while one is already queued is dropped rather than stacked.
    """
    global _pending

    if _lock.acquire(blocking=False):
        _pending = None  # supersede any stale queue entry
    else:
        if sources is not None:  # fast sweep never preempts
            return False
        if _pending is not None:  # a deep scan is already queued — drop this one
            log.info("deep scan already queued — ignoring duplicate request")
            return False
        log.info("deep scan queued — asking the running sweep to stop")
        _stop.set()
        _pending = (sources, digest)
        return False

    try:
        state.update(running=True, started_at=datetime.now(UTC).isoformat(), result=None, error=None)
        threading.Thread(target=_worker, args=(sources, digest), daemon=True).start()
    except Exception:
        state["running"] = False
        _lock.release()
        raise
    return True


# A deep scan waiting for the lock, started by _worker once the sweep unwinds.
_pending: tuple[list[str] | None, bool] | None = None


def _drain_pending() -> None:
    """Called by _worker after releasing the lock: start a queued deep scan."""
    global _pending

    pending, _pending = _pending, None
    if pending is None:
        return
    log.info("starting queued deep scan now that the sweep unwound")
    try:
        try_start_scan(*pending)
    except Exception:  # noqa: BLE001 — a queued scan must never kill the worker
        log.exception("queued deep scan failed to start")
