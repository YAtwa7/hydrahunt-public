"""FastAPI entrypoint — dashboard backend + cockpit API.

Port safety: probes JOBHUNT_PORT (default 8420) and walks forward to 8429
if occupied. Binds 127.0.0.1 only — never network-exposed.
Single instance enforced via PID lockfile (see _pid_alive for why the
liveness probe is platform-split).
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import socket
import sqlite3
import sys
from ctypes import wintypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import ROOT, load_config
from app.db import init_db

(ROOT / "logs").mkdir(exist_ok=True)


class _AsciiFormatter(logging.Formatter):
    """Replace non-ASCII punctuation so logs stay readable on any console.

    The Windows console (CP1252) mangles UTF-8 em-dashes and friends into
    mojibake; this keeps every handler output pure ASCII.
    """

    _REPLACEMENTS = {
        "—": "-",  # em dash
        "–": "-",  # en dash
        "‘": "'", "’": "'",
        "“": '"', "”": '"',
        "…": "...",
        "→": "->",
        "✓": "ok",
        "✗": "x",
    }

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        for src, dst in self._REPLACEMENTS.items():
            msg = msg.replace(src, dst)
        return msg


_ascii_fmt = _AsciiFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "logs" / "hydrahunt.log", encoding="utf-8"),
    ],
)
for _h in logging.getLogger().handlers:
    _h.setFormatter(_ascii_fmt)
log = logging.getLogger("jobhunt")

LOCKFILE = ROOT / "data" / "jobhunt.pid"

app = FastAPI(title="HydraHunt", version=__version__)

# Dashboard (built React app) — served from the same process
_DIST = ROOT / "frontend" / "dist"
if _DIST.exists():
    app.mount("/app", StaticFiles(directory=str(_DIST), html=True), name="dashboard")


@app.get("/")
def root() -> dict[str, str]:
    return {"dashboard": "/app/index.html", "docs": "/docs", "health": "/healthz"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/config/summary")
def config_summary() -> dict:
    cfg = load_config()
    return {
        "excluded_regions": cfg.excluded_regions,
        "target_titles": cfg.target_titles,
        "collectors": {
            name: cfg.collectors_enabled(name)
            for name in ("jobspy", "remote_boards", "ats_companies", "reddit", "hackernews", "dorklab")
        },
    }


_SCAN_QUERY = Query(default=None)


@app.post("/api/scan")
def trigger_scan(sources: list[str] | None = _SCAN_QUERY) -> dict:
    """Start a background scan. Poll /api/scan/status for progress."""
    from app import scancontrol

    if not scancontrol.try_start_scan(sources):
        return {"started": False, "reason": "scan already running"}
    return {"started": True, "started_at": scancontrol.state["started_at"]}


@app.get("/api/scan/status")
def scan_status() -> dict:
    from app import progress, scancontrol
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        last = conn.execute(
            "SELECT * FROM scans WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        total = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        durations = conn.execute(
            """SELECT started_at, finished_at FROM scans
               WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 5"""
        ).fetchall()
    finally:
        conn.close()

    # ETA: average duration of the last 5 completed scans minus elapsed
    avg_seconds = None
    if durations:
        spans = []
        for d in durations:
            try:
                t0 = datetime.fromisoformat(d["started_at"])
                t1 = datetime.fromisoformat(d["finished_at"])
                spans.append((t1 - t0).total_seconds())
            except (TypeError, ValueError):
                continue
        if spans:
            avg_seconds = sum(spans) / len(spans)

    elapsed = None
    if scancontrol.state["started_at"] and scancontrol.state["running"]:
        elapsed = (
            datetime.now(UTC) - datetime.fromisoformat(scancontrol.state["started_at"])
        ).total_seconds()

    return {
        "running": scancontrol.state["running"],
        "started_at": scancontrol.state["started_at"],
        "result": scancontrol.state["result"],
        "error": scancontrol.state["error"],
        "total_jobs": total,
        "last_scan": dict(last) if last else None,
        "progress": progress.snapshot(),
        "elapsed_s": elapsed,
        "eta_s": max(0.0, avg_seconds - elapsed) if avg_seconds and elapsed else None,
        "avg_scan_s": avg_seconds,
    }


@app.get("/api/scans")
def scan_history(limit: int = 20) -> list[dict]:
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        rows = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/jobs")
def list_jobs(
    tier: str | None = None,
    status: str | None = None,
    source: str | None = None,
    sort: str = "date",
    limit: int = 100,
) -> list[dict]:
    from app.db import POOL_WORTHY_SQL, connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        q = "SELECT * FROM jobs WHERE 1=1"
        params: list = []
        if tier:
            q += " AND tier=?"
            params.append(tier)
        if status:
            q += " AND status=?"
            params.append(status)
        else:
            # default view = the live pool; hidden junk stays out unless
            # explicitly requested (matches the scheduler's visible count).
            # EXCLUDED is a hard no — excluded defensively even if a row's
            # status hasn't been flipped to hidden yet — and the "reachability
            # bar" keeps unverifiable, location-less postings out too.
            # Jobs the user is tracking (applied/interview/offer) always show.
            q += (
                " AND (status IN ('applied','interview','offer') OR ("
                "status != ? AND COALESCE(location_tag,'') != 'EXCLUDED' AND "
                + POOL_WORTHY_SQL
                + "))"
            )
            params.append("hidden")
        if source:
            q += " AND source LIKE ?"
            params.append(f"%{source}%")
        if sort == "score":
            q += " ORDER BY COALESCE(llm_score, rule_score) DESC"
        else:
            q += " ORDER BY first_seen_at DESC"
        q += " LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.patch("/api/jobs/{job_id}/status")
def update_status(job_id: int, status: str) -> dict:
    from app.db import connect

    allowed = {"new", "viewed", "applied", "interview", "offer", "rejected", "hidden"}
    if status not in allowed:
        return {"error": f"status must be one of {sorted(allowed)}"}
    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status=?, updated_at=datetime('now') WHERE id=?", (status, job_id)
        )
        conn.commit()
        return {"id": job_id, "status": status}
    finally:
        conn.close()


@app.get("/api/companies")
def list_companies() -> list[dict]:
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        rows = conn.execute(
            """SELECT c.*, (SELECT COUNT(*) FROM jobs j
                            WHERE lower(j.company) = lower(c.name)) AS jobs_found
               FROM companies c ORDER BY blacklisted, enabled DESC, name"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/companies")
def add_company(name: str, careers_url: str = "") -> dict:
    """Add a company; ATS auto-detected by slug probe."""
    from app.db import connect
    from app.pipeline.companies import detect_ats

    cfg = load_config()
    detected = detect_ats(name) or ("", "")
    conn = connect(cfg.db_path)
    try:
        conn.execute(
            """INSERT INTO companies (name, ats, board_token, enabled, careers_url)
               VALUES (?,?,?,1,?)
               ON CONFLICT(name) DO UPDATE SET enabled=1, blacklisted=0""",
            (name, detected[0], detected[1], careers_url),
        )
        conn.commit()
        return {"name": name, "ats": detected[0], "board_token": detected[1]}
    finally:
        conn.close()


@app.patch("/api/companies/{company_id}")
def update_company(company_id: int, enabled: bool | None = None, blacklisted: bool | None = None) -> dict:
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        if enabled is not None:
            conn.execute("UPDATE companies SET enabled=? WHERE id=?", (int(enabled), company_id))
        if blacklisted is not None:
            conn.execute(
                "UPDATE companies SET blacklisted=? WHERE id=?", (int(blacklisted), company_id)
            )
        conn.commit()
        return {"id": company_id, "enabled": enabled, "blacklisted": blacklisted}
    finally:
        conn.close()


@app.get("/api/dorks")
def list_dorks() -> list[dict]:
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        rows = conn.execute(
            """SELECT id, query, family, fitness, generation, runs,
                      total_results, valid_results, retired
               FROM dorks ORDER BY retired, fitness DESC, valid_results DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/dorks/evolve")
def trigger_evolution() -> dict:
    from app.db import connect
    from app.dorklab.evolve import evolve

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        return evolve(conn, cfg)
    finally:
        conn.close()


@app.get("/api/scheduler")
def scheduler_info() -> dict:
    from app.scheduler import next_runs

    return {"jobs": next_runs()}


@app.post("/api/jobs/{job_id}/draft")
def generate_job_draft(job_id: int) -> dict:
    """Generate (or return cached) application draft. Never auto-submits."""
    from app.apply.drafter import generate_draft, get_cached, save_draft
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return {"error": f"job {job_id} not found"}
        cached = get_cached(conn, job_id)
        if cached:
            return cached
        draft = generate_draft(dict(job), cfg)
        if not draft:
            return {"error": "draft generation failed (LLM unavailable) — try again later"}
        save_draft(conn, job_id, draft)
        return draft
    finally:
        conn.close()


@app.get("/api/jobs/{job_id}/draft")
def get_job_draft(job_id: int) -> dict:
    from app.apply.drafter import get_cached
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        draft = get_cached(conn, job_id)
        return draft or {"error": "no draft yet — POST to generate"}
    finally:
        conn.close()


@app.delete("/api/jobs/{job_id}/draft")
def delete_job_draft(job_id: int) -> dict:
    from app.db import connect

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        conn.execute("DELETE FROM drafts WHERE job_id=?", (job_id,))
        conn.commit()
        return {"deleted": job_id}
    finally:
        conn.close()


@app.get("/api/stats")
def stats() -> dict:
    from app.db import POOL_WORTHY_SQL, connect, weekly_kept_by_week

    cfg = load_config()
    conn = connect(cfg.db_path)
    try:
        # counts must describe the pool the dashboard actually renders
        row = conn.execute(
            f"""SELECT COUNT(*) total,
                      SUM(tier='A') a, SUM(tier='B') b, SUM(tier='C') c,
                      SUM(status='applied') applied
               FROM jobs
               WHERE status != 'hidden' AND COALESCE(location_tag,'') != 'EXCLUDED'
                 AND {POOL_WORTHY_SQL}"""
        ).fetchone()
        last_scan = conn.execute(
            # same rule as /api/scan/status — an unfinished row is not a
            # completed scan and must not be reported as the last one
            "SELECT * FROM scans WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "jobs": dict(row) if row else {},
            "last_scan": dict(last_scan) if last_scan else None,
            "kept_by_week": weekly_kept_by_week(conn),
        }
    finally:
        conn.close()


@app.get("/api/answers")
def answers() -> list[dict]:
    """Reusable, evidence-bounded application answers from the profile."""
    return load_config().profile.get("answers", [])


def find_free_port(preferred: int, host: str, attempts: int = 10) -> int:
    """Return preferred port or the next free one (8420..8429 by default)."""
    for offset in range(attempts):
        port = preferred + offset
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind((host, port))
            except OSError:
                log.warning("Port %d is occupied, trying %d", port, port + 1)
                continue
            return port
    raise RuntimeError(f"No free port in range {preferred}-{preferred + attempts - 1}")


def _configure_kernel32(kernel32: Any) -> Any:
    """Declare the signatures we call.

    Without argtypes, ctypes converts Python ints as C `long` and RAISES
    OverflowError for any PID above 2**32-1. That would let a corrupt lockfile
    (which `acquire_lock` happily `int()`-parses) crash startup with a
    traceback — re-creating the exact failure mode this fix removes. Declaring
    the signatures turns an out-of-range PID into a plain NULL handle.
    """
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _load_kernel32() -> Any | None:
    """Windows kernel32 handle, or None on POSIX (no ctypes.WinDLL there).

    Resolved via getattr rather than a direct ctypes.WinDLL attribute so this
    type-checks on both platforms — CI runs mypy on ubuntu, where WinDLL does
    not exist as an attribute.
    """
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:  # pragma: no cover - POSIX
        return None
    try:
        return _configure_kernel32(win_dll("kernel32", use_last_error=True))
    except (AttributeError, OSError):  # pragma: no cover - defensive
        return None


_KERNEL32: Any | None = _load_kernel32()

# `ctypes.get_last_error` only exists on Windows in typeshed, and CI runs mypy
# on ubuntu. Resolve it through getattr so both platforms type-check.
_get_last_error: Any = getattr(ctypes, "get_last_error", lambda: 0)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_ACCESS_DENIED = 5
_MAX_PID = 2**32


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness via OpenProcess + GetExitCodeProcess.

    A handle alone is NOT proof of life: Windows keeps the process object alive
    until its last handle closes, so a just-exited PID still opens fine. The
    process must also still report STILL_ACTIVE.
    """
    kernel32 = _KERNEL32
    # Range-check first: an impossible PID needs no OS support to reject, so
    # this must run before the kernel32 guard (and it is what keeps a corrupt
    # lockfile from ever reaching the Win32 layer).
    if not 0 < pid < _MAX_PID:
        return False
    if kernel32 is None:  # pragma: no cover - kernel32 failed to load
        return True  # fail safe: assume alive so we never double-start
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # ACCESS_DENIED means the process exists, we just may not inspect it.
        return _get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    except (OSError, ValueError):  # pragma: no cover - defensive
        return True  # unknown -> assume alive, never double-start
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    """Is `pid` a live process? Never raises.

    `os.kill(pid, 0)` is the POSIX liveness probe but is NOT one on Windows
    (see INC-005):

    - a *just-exited* PID still answers cleanly, because Windows keeps the
      process object alive until its last handle closes — so a crashed server
      looks alive and blocks the next boot with a misleading "another instance"
      error, sometimes indefinitely;
    - a long-dead PID raises a bare `OSError(errno 22 / WinError 87)` which is
      NOT a `ProcessLookupError`, so narrow exception matching missed it.

    So Windows uses OpenProcess/GetExitCodeProcess and POSIX keeps os.kill.
    On POSIX, PermissionError means the process exists but is not ours to
    signal, which still means "alive".
    """
    if os.name == "nt":
        # Never fall back to os.kill on Windows — it is not a read-only probe
        # there. If kernel32 failed to load, _pid_alive_windows already
        # returned "alive", so we fail safe instead of risking a second start.
        return _pid_alive_windows(pid)
    if not 0 < pid < _MAX_PID:
        return False  # no such PID can be live
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # exists, just not ours to signal (EPERM)
    except OSError:  # noqa: BLE001 — covers ProcessLookupError/ESRCH
        return False  # no such process
    return True


def acquire_lock() -> None:
    """Prevent double instances via PID lockfile.

    A stale lockfile (server killed, PID since reused or exited) is taken over
    silently — see INC-005.
    """
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCKFILE.exists():
        try:
            old_pid = int(LOCKFILE.read_text().strip())
        except ValueError:
            pass  # unreadable/garbage lockfile — take over
        else:
            if _pid_alive(old_pid):
                log.error(
                    "Another instance is already running (PID %d). Exiting.", old_pid
                )
                log.error(
                    "If nothing is running, the PID was reused by an unrelated "
                    "process — delete %s to force a start.",
                    LOCKFILE,
                )
                sys.exit(2)
            log.info("Stale lockfile from PID %d — taking over.", old_pid)
    LOCKFILE.write_text(str(os.getpid()))


def release_lock() -> None:
    LOCKFILE.unlink(missing_ok=True)


def reap_orphaned_scans(db_path: str | Path, min_age_s: int = 3600) -> int:
    """Close out scans that were killed mid-flight (service restart).

    A scan row with no finished_at means the process died while it was running.
    Left open, it lingers as the newest row and the dashboard reports it as the
    current scan forever.

    Only rows older than min_age_s are touched, so this can never stamp a
    legitimately in-flight scan (e.g. a second instance that slipped past the
    PID lock) as finished.
    """
    cutoff = (datetime.now(UTC) - timedelta(seconds=min_age_s)).isoformat()
    from app.db import connect

    conn = connect(db_path)
    try:
        # Bind a real UTC ISO timestamp: every other finished_at is written by
        # run_scan as datetime.now(UTC).isoformat(). Using sqlite's datetime()
        # here would write a naive space-separated value into the same column and
        # break the offset-aware subtraction the ETA average performs.
        cur = conn.execute(
            "UPDATE scans SET finished_at=?, error='interrupted by restart' "
            "WHERE finished_at IS NULL AND started_at < ?",
            (datetime.now(UTC).isoformat(), cutoff),
        )
        conn.commit()
        n = cur.rowcount or 0
        if n:
            log.info("reaped %d orphaned scan row(s) from a previous run", n)
        return n
    except sqlite3.Error:
        # Never let cleanup stop the service from starting.
        log.warning("orphan reaper failed", exc_info=True)
        return 0
    finally:
        conn.close()


def main() -> None:
    from app.notify.bot import start_if_configured
    from app.scheduler import start as start_scheduler

    cfg = load_config()
    host, preferred = cfg.settings.host, cfg.settings.port
    acquire_lock()
    try:
        port = find_free_port(preferred, host)
        init_db(cfg.db_path)
        reap_orphaned_scans(cfg.db_path)
        start_scheduler(cfg)
        start_if_configured(cfg.settings.discord_bot_token)
        log.info("HydraHunt listening on http://%s:%d (pid %d)", host, port, os.getpid())
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        release_lock()


if __name__ == "__main__":
    main()
