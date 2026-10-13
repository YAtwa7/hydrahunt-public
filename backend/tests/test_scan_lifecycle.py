"""Scan-lifecycle regression tests: orphan reaping + deep-scan preemption +
PID-lockfile handling.

All are fixes for failure modes that are invisible until they hurt:
- an orphaned scan row (killed mid-flight) was reported as the current scan;
- the deep-scan preemption path could block an async caller and used to keep
  filtering/scoring/LLM/notify running after being told to stop;
- a stale PID lockfile left by a killed server crashed the next boot on Windows.
"""

from __future__ import annotations

import contextlib
import errno
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app import main as main_mod
from app import scancontrol
from app.db import connect, init_db
from app.main import reap_orphaned_scans


def _scan_row(conn, *, finished: str | None, age_s: int = 7200, error: str = "") -> int:
    started = (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat()
    cur = conn.execute(
        "INSERT INTO scans (started_at, finished_at, jobs_found, jobs_new, error) "
        "VALUES (?,?,0,0,?)",
        (started, finished, error),
    )
    conn.commit()
    return int(cur.lastrowid)


# ------------------------------------------------------------- orphan reaper
def test_reaper_closes_unfinished_rows(tmp_path):
    db = tmp_path / "r.db"
    init_db(db)
    conn = connect(db)
    orphan = _scan_row(conn, finished=None, age_s=7200)
    conn.close()

    assert reap_orphaned_scans(db) == 1

    conn = connect(db)
    row = conn.execute("SELECT finished_at, error FROM scans WHERE id=?", (orphan,)).fetchone()
    conn.close()
    assert row["finished_at"] is not None
    assert "interrupted" in row["error"]


def test_reaper_leaves_finished_rows_untouched(tmp_path):
    db = tmp_path / "r.db"
    init_db(db)
    conn = connect(db)
    done_at = datetime.now(UTC).isoformat()
    done = _scan_row(conn, finished=done_at, age_s=7200)
    conn.close()

    assert reap_orphaned_scans(db) == 0

    conn = connect(db)
    row = conn.execute("SELECT finished_at FROM scans WHERE id=?", (done,)).fetchone()
    conn.close()
    assert row["finished_at"] == done_at


def test_reaper_never_stamps_a_live_scan(tmp_path):
    """A young unfinished row is a scan that is genuinely in flight."""
    db = tmp_path / "r.db"
    init_db(db)
    conn = connect(db)
    live = _scan_row(conn, finished=None, age_s=5)
    conn.close()

    assert reap_orphaned_scans(db) == 0

    conn = connect(db)
    assert conn.execute(
        "SELECT finished_at FROM scans WHERE id=?", (live,)
    ).fetchone()["finished_at"] is None
    conn.close()


def test_reaper_writes_iso_utc_not_naive_sqlite_time(tmp_path):
    """The ETA average subtracts finished_at - started_at as aware datetimes;
    a naive sqlite datetime() would raise TypeError there."""
    db = tmp_path / "r.db"
    init_db(db)
    conn = connect(db)
    orphan = _scan_row(conn, finished=None, age_s=7200)
    conn.close()

    reap_orphaned_scans(db)

    conn = connect(db)
    row = conn.execute(
        "SELECT started_at, finished_at FROM scans WHERE id=?", (orphan,)
    ).fetchone()
    conn.close()
    t0 = datetime.fromisoformat(row["started_at"])
    t1 = datetime.fromisoformat(row["finished_at"])
    assert t0.tzinfo is not None and t1.tzinfo is not None
    assert (t1 - t0).total_seconds() > 0  # would TypeError if either were naive


def test_scan_status_ignores_unfinished_rows(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.config import AppConfig

    db = tmp_path / "s.db"
    init_db(db)
    conn = connect(db)
    _scan_row(conn, finished=datetime.now(UTC).isoformat(), age_s=7200)
    _scan_row(conn, finished=None, age_s=7200)  # orphan, newer id
    conn.close()

    monkeypatch.setattr(
        main_mod, "load_config", lambda: AppConfig(raw={"app": {"db_path": str(db)}})
    )
    client = TestClient(main_mod.app)
    body = client.get("/api/scan/status").json()
    assert body["last_scan"]["finished_at"] is not None
    # and the stats endpoint agrees with it
    stats = client.get("/api/stats").json()
    assert stats["last_scan"]["finished_at"] is not None


# --------------------------------------------------------------- preemption
@pytest.fixture
def _clean_scancontrol():
    scancontrol._pending = None
    scancontrol._stop.clear()
    yield
    scancontrol._pending = None
    scancontrol._stop.clear()
    if scancontrol._lock.locked():
        with contextlib.suppress(RuntimeError):
            scancontrol._lock.release()


def test_preemption_never_blocks_the_caller(_clean_scancontrol):
    """The Discord bot calls this from an async handler on the event loop.
    A blocking acquire here would freeze slash commands + gateway heartbeats."""
    assert scancontrol._lock.acquire(blocking=False)  # pretend a fast sweep holds it
    called = threading.Event()

    def caller():
        started = scancontrol.try_start_scan()  # deep scan, sources=None
        called.set()
        return started

    t = threading.Thread(target=caller, daemon=True)
    t.start()
    assert called.wait(timeout=2.0), "try_start_scan blocked the caller"
    t.join(timeout=2.0)
    # the deep scan was queued, not started, and not lost
    assert scancontrol._pending is not None


def test_fast_sweep_never_preempts(_clean_scancontrol):
    assert scancontrol._lock.acquire(blocking=False)
    assert scancontrol.try_start_scan(sources=["rocketship"]) is False
    assert scancontrol._pending is None


def test_duplicate_deep_requests_are_dropped_not_stacked(_clean_scancontrol):
    """Two queued deep scans would serialize into two full JobSpy runs."""
    assert scancontrol._lock.acquire(blocking=False)
    scancontrol.try_start_scan()  # first deep request -> queued
    scancontrol.try_start_scan()  # duplicate -> dropped
    assert scancontrol._pending is not None


def test_drain_starts_the_queued_deep_scan(_clean_scancontrol):
    assert scancontrol._lock.acquire(blocking=False)
    scancontrol.try_start_scan()
    scancontrol._lock.release()  # sweep unwound
    scancontrol._drain_pending()
    assert scancontrol._pending is None
    assert scancontrol.state["running"] is True
    # release whatever drain started so the next test starts clean
    if scancontrol._lock.locked():
        scancontrol._lock.release()
    scancontrol.state["running"] = False


def test_reaper_survives_a_locked_database(tmp_path):
    """A locked DB must not stop the service from booting."""
    db = tmp_path / "r.db"
    init_db(db)
    blocker = sqlite3.connect(db)
    blocker.execute("BEGIN EXCLUSIVE")
    blocker.execute("CREATE TABLE IF NOT EXISTS _hold (x)")
    try:
        # busytimeout is 5s in connect(); this asserts we degrade to a warning
        assert reap_orphaned_scans(db) in (0, 1)
    finally:
        blocker.rollback()
        blocker.close()


# ---------------------------------------------------------------
# PID lockfile (INC-005) — a stale lockfile must not block boot.
# ---------------------------------------------------------------


@pytest.fixture
def _lockfile(tmp_path, monkeypatch):
    """Point LOCKFILE at a temp path so we never touch the real one."""
    lf = tmp_path / "jobhunt.pid"
    monkeypatch.setattr(main_mod, "LOCKFILE", lf)
    return lf


def test_pid_alive_detects_a_live_process():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert main_mod._pid_alive(p.pid) is True
    finally:
        p.kill()
        p.wait()


def test_pid_alive_detects_a_just_exited_process():
    """The core Windows bug: os.kill(pid,0) answers cleanly for these.

    A crashed server must be detected as dead straight away, otherwise the next
    boot refuses to start with a misleading "another instance is running".
    """
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert main_mod._pid_alive(p.pid) is False


def test_pid_alive_detects_a_never_valid_pid():
    assert main_mod._pid_alive(0x7FFFFFF0) is False


def test_pid_alive_posix_eperm_counts_as_alive(monkeypatch):
    """On POSIX, EPERM means the process exists but is not ours to signal."""
    monkeypatch.setattr(main_mod, "_KERNEL32", None)
    monkeypatch.setattr(main_mod.os, "name", "posix")

    def fake_kill(pid, sig):
        raise PermissionError(errno.EPERM, "operation not permitted")

    monkeypatch.setattr(main_mod.os, "kill", fake_kill)
    assert main_mod._pid_alive(4242) is True


def test_pid_alive_posix_esrch_counts_as_dead(monkeypatch):
    monkeypatch.setattr(main_mod, "_KERNEL32", None)
    monkeypatch.setattr(main_mod.os, "name", "posix")

    def fake_kill(pid, sig):
        raise ProcessLookupError(errno.ESRCH, "no such process")

    monkeypatch.setattr(main_mod.os, "kill", fake_kill)
    assert main_mod._pid_alive(4242) is False


def test_pid_alive_posix_bare_oserror_counts_as_dead(monkeypatch):
    """The other Windows symptom, forced through the POSIX branch.

    os.kill on Windows raises OSError(22/87), which is not ProcessLookupError;
    a narrow except clause missed it and crashed startup instead of taking over.
    """
    monkeypatch.setattr(main_mod, "_KERNEL32", None)
    monkeypatch.setattr(main_mod.os, "name", "posix")

    def fake_kill(pid, sig):
        raise OSError(errno.EINVAL, "invalid parameter")

    monkeypatch.setattr(main_mod.os, "kill", fake_kill)
    assert main_mod._pid_alive(4242) is False


def test_acquire_lock_takes_over_a_stale_lockfile(_lockfile):
    """The regression: a stale lock must be taken over, not raise or exit."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    _lockfile.write_text(str(p.pid))

    main_mod.acquire_lock()  # must not raise, must not SystemExit

    assert _lockfile.read_text().strip() == str(os.getpid())


def test_acquire_lock_takes_over_a_garbage_lockfile(_lockfile):
    _lockfile.write_text("not-a-pid")
    main_mod.acquire_lock()
    assert _lockfile.read_text().strip() == str(os.getpid())


def test_acquire_lock_takes_over_an_empty_lockfile(_lockfile):
    _lockfile.write_text("")
    main_mod.acquire_lock()
    assert _lockfile.read_text().strip() == str(os.getpid())


def test_acquire_lock_refuses_when_a_live_instance_owns_it(_lockfile):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        _lockfile.write_text(str(p.pid))
        with pytest.raises(SystemExit) as exc:
            main_mod.acquire_lock()
        assert exc.value.code == 2
        assert _lockfile.read_text().strip() == str(p.pid)  # left untouched
    finally:
        p.kill()
        p.wait()


def test_acquire_lock_round_trips_with_release(_lockfile):
    main_mod.acquire_lock()
    assert _lockfile.read_text().strip() == str(os.getpid())
    main_mod.release_lock()
    assert not _lockfile.exists()


def test_release_lock_is_safe_when_already_gone(_lockfile):
    _lockfile.write_text("12345")
    main_mod.release_lock()
    main_mod.release_lock()  # idempotent — must not raise
    assert not _lockfile.exists()


# ---------------------------------------------------------------
# Windows liveness path, exercised through a kernel32 stub so CI
# (ubuntu) covers it too. INC-005's prevention rule: platform-specific
# behaviour needs a test of the real code path, not only the
# monkeypatched POSIX branch.
# ---------------------------------------------------------------


class _FakeKernel32:
    """Minimal kernel32 stand-in recording every handle it hands out."""

    def __init__(self, *, exit_code=259, open_ok=True, getexit_ok=True, last_error=0):
        self._exit_code = exit_code
        self._open_ok = open_ok
        self._getexit_ok = getexit_ok
        self.opened = []
        self.closed = []
        self._last_error = last_error

        self.OpenProcess = self._open_process
        self.GetExitCodeProcess = self._get_exit_code
        self.CloseHandle = self._close

    def _open_process(self, access, inherit, pid):
        self.opened.append((access, bool(inherit), pid))
        return 0x1234 if self._open_ok else 0

    def _get_exit_code(self, handle, code_ptr):
        if not self._getexit_ok:
            return 0  # failure
        code_ptr._obj.value = self._exit_code
        return 1

    def _close(self, handle):
        self.closed.append(handle)
        return 1


def _stub_kernel32(monkeypatch, last_error=0, **kw):
    """Install a fake kernel32 and force the Windows branch."""
    fake = _FakeKernel32(last_error=last_error, **kw)
    monkeypatch.setattr(main_mod, "_KERNEL32", fake)
    monkeypatch.setattr(main_mod.os, "name", "nt")
    return fake


def test_windows_still_active_means_alive(monkeypatch):
    k32 = _stub_kernel32(monkeypatch, exit_code=259)
    assert main_mod._pid_alive(1234) is True
    assert k32.closed == [0x1234]  # handle released


@pytest.mark.parametrize("code", [0, 1, 1000])
def test_windows_exited_process_means_dead(monkeypatch, code):
    k32 = _stub_kernel32(monkeypatch, exit_code=code)
    assert main_mod._pid_alive(1234) is False
    assert k32.closed == [0x1234]


def test_windows_access_denied_means_alive(monkeypatch):
    """We may not inspect it, but it exists — refusing to start is safe."""
    _stub_kernel32(monkeypatch, open_ok=False, last_error=5)
    monkeypatch.setattr(main_mod, "_get_last_error", lambda: 5)
    assert main_mod._pid_alive(1234) is True


def test_windows_other_open_failure_means_dead(monkeypatch):
    _stub_kernel32(monkeypatch, open_ok=False, last_error=87)
    monkeypatch.setattr(main_mod, "_get_last_error", lambda: 87)
    assert main_mod._pid_alive(1234) is False


def test_windows_handle_is_closed_when_getexitcode_fails(monkeypatch):
    k32 = _stub_kernel32(monkeypatch, getexit_ok=False)
    assert main_mod._pid_alive(1234) is False
    assert k32.closed == [0x1234]  # no handle leak on the failure path


def test_windows_never_opens_a_handle_for_an_impossible_pid(monkeypatch):
    """A corrupt lockfile must not reach the Win32 layer (overflow guard)."""
    k32 = _stub_kernel32(monkeypatch)
    assert main_mod._pid_alive(0) is False
    assert main_mod._pid_alive(-1) is False
    assert main_mod._pid_alive(2**40) is False
    assert k32.opened == []


def test_windows_missing_kernel32_fails_safe(monkeypatch):
    """Never fall back to os.kill on Windows — assume alive instead."""
    monkeypatch.setattr(main_mod, "_KERNEL32", None)
    monkeypatch.setattr(main_mod.os, "name", "nt")

    def forbidden(pid, sig):
        raise AssertionError("os.kill must never be called on Windows")

    monkeypatch.setattr(main_mod.os, "kill", forbidden)
    assert main_mod._pid_alive(1234) is True


def test_configure_kernel32_declares_signatures():
    """Undeclared argtypes make ctypes raise on PIDs above 2**32-1."""

    class _Unconfigured:
        pass

    stub = _Unconfigured()
    for name in ("OpenProcess", "GetExitCodeProcess", "CloseHandle"):
        setattr(stub, name, lambda *a, **k: None)
    main_mod._configure_kernel32(stub)
    assert stub.OpenProcess.argtypes is not None
    assert stub.OpenProcess.restype is not None


def test_acquire_lock_takes_over_on_the_bare_oserror_path(monkeypatch, _lockfile):
    """End-to-end regression for failure mode (a).

    Pre-fix this raised an escaped OSError and crashed startup; the old except
    tuple was (ValueError, ProcessLookupError, PermissionError), which a bare
    OSError(errno 22 / WinError 87) escapes.
    """
    monkeypatch.setattr(main_mod, "_KERNEL32", None)
    monkeypatch.setattr(main_mod.os, "name", "posix")

    def fake_kill(pid, sig):
        raise OSError(errno.EINVAL, "invalid parameter")

    monkeypatch.setattr(main_mod.os, "kill", fake_kill)
    _lockfile.write_text("999999")

    main_mod.acquire_lock()  # must not raise

    assert _lockfile.read_text().strip() == str(os.getpid())


def test_acquire_lock_takes_over_an_absurd_pid(monkeypatch, _lockfile):
    """A garbage lockfile holding a 20-digit PID must not crash startup.

    The range guard runs before the kernel32 guard, so this is rejected even
    with no kernel32 available.
    """
    monkeypatch.setattr(main_mod, "_KERNEL32", None)
    monkeypatch.setattr(main_mod.os, "name", "nt")
    _lockfile.write_text("99999999999999999999")
    main_mod.acquire_lock()
    assert _lockfile.read_text().strip() == str(os.getpid())
