# Incidents & Lessons Learned

Six bugs that were invisible until they hurt, and the rule each one left behind.
Read the one covering your area before you touch it — every entry exists because
something looked correct and wasn't.

| Entry | Area | The trap |
|---|---|---|
| INC-001 | `db.py` | A relative config path resolved against the CWD, not the repo root |
| INC-002 | `runner.py` | The scanner ran before the schema existed |
| INC-003 | `scoring/llm.py` | A free model returned `content: null` and killed an 8-minute scan |
| INC-004 | `scoring/llm.py` | Retrying dead models sequentially turned a 2-minute scan into 39 |
| INC-005 | `main.py` | `os.kill(pid, 0)` is not a liveness probe on Windows |

Each entry runs: what you would have seen → why it happened → the fix → **the
prevention rule**, which is the part that matters. Every rule is backed by a
test that fails against the old code.

> The `Issue:` numbers in each entry refer to the upstream development tracker
> and won't resolve in this repository.

---

### INC-005 — Stale PID lockfile blocked restart on Windows (2026-10-13)
- **Issue:** #30
- **What happened:** Force-killing the server (or a hard crash) left `data/jobhunt.pid` behind and the next boot failed. It failed in **two** different ways depending on timing: sometimes `OSError: [WinError 87] The parameter is incorrect` escaped and crashed startup; sometimes it printed the misleading `Another instance is already running (PID n). Exiting.` and refused to start at all, even though nothing was running.
- **Root cause:** `acquire_lock()` probed liveness with `os.kill(pid, 0)` and matched failures against `(ValueError, ProcessLookupError, PermissionError)`. That probe is wrong on Windows in two independent ways: (1) a *just-exited* PID still answers cleanly, because Windows keeps the process object alive until its last handle closes — so a crashed server reads as alive and blocks boot indefinitely; (2) a long-dead PID raises a bare `OSError(errno 22 / WinError 87)` which is **not** a `ProcessLookupError`, so the narrow `except` missed it and the exception propagated. `os.kill(pid, 0)` is only a valid liveness probe on POSIX.
- **Fix:** New `_pid_alive()` with a platform split. Windows uses `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` + `GetExitCodeProcess()` (a handle is not enough — the process must still report `STILL_ACTIVE`); POSIX keeps `os.kill(pid, 0)` and treats `PermissionError`/EPERM as *alive* (the process exists, we just may not signal it). `kernel32` is resolved through `getattr(ctypes, "WinDLL", None)` so mypy stays clean on the ubuntu CI runner, where `WinDLL` does not exist. `acquire_lock()` now logs `Stale lockfile from PID n — taking over` instead of failing silently or crashing.
- **Prevention rule:** Never use `os.kill(pid, 0)` as a liveness check on Windows — it is not one. Match `OSError` (never a narrow tuple) for "process is gone", and distinguish "gone" from "exists but not ours". Platform-specific behaviour needs a test that exercises the real platform path, not only monkeypatched branches.

### INC-001 — Relative DB path resolved against CWD (2026-10-05)
- **Issue:** #3
- **What happened:** First live scan created `backend/data/jobhunt.db` instead of `data/jobhunt.db` because `db_path: data/jobhunt.db` was resolved against the process working directory.
- **Root cause:** Config returned the raw relative path; callers opened it directly.
- **Fix:** `AppConfig.db_path` property resolves relative paths against repo `ROOT`; all callers use it.
- **Prevention rule:** Any path from config must go through a resolver that anchors to `ROOT`. Added `.gitignore` coverage for `data/` so a stray DB can never be committed.

### INC-004 — 39-minute scans: dead-model retries × sequential LLM calls (2026-10-08)
- **Issue:** #10
- **What happened:** A fast sweep took 39 min — collectors finished in ~2 min, then "AI scoring" ran ~2 min per job.
- **Root cause:** Two compounding design flaws: (1) the fallback chain retried rotated/dead free models from scratch for every one of the 20 shortlisted jobs; (2) LLM calls ran strictly sequentially.
- **Fix:** First-working-model memo (`_last_good` — the live model goes first for the rest of the process) + concurrent LLM stage (3 workers, fetch-only workers, main thread writes — sqlite connections can't cross threads).
- **Prevention rule:** Any retry chain over unreliable external services must cache its first success. Any per-item external calls must be parallelized or bounded.

### INC-003 — LLM null content crashed an entire 8-minute scan (2026-10-05)
- **Issue:** #8
- **What happened:** Scan died with `the JSON object must be str, bytes or bytearray, not NoneType` ~8 minutes in, losing the whole run.
- **Root cause:** A free-tier model returned `content: null` (refusal/reasoning-only). `json.loads(None)` raises `TypeError`, which was not in the caught exception tuple — so one bad model response propagated out of the scorer and killed the scan worker.
- **Fix:** Null-content guard + catch-all per model in `_call_provider`; runner wraps the LLM stage so it can never kill a scan; scans table now records `CRASHED: <err>` on failure.
- **Prevention rule:** Any external-API response parsing must treat every field as potentially null; provider calls catch `Exception`, never specific types only. Regression tests in `test_llm_robustness.py`.

### INC-002 — run_scan crashed on uninitialized DB (2026-10-05)
- **Issue:** #3
- **What happened:** First smoke test died with `sqlite3.OperationalError: no such table: scans`.
- **Root cause:** `init_db()` was only called in `main()`; the runner assumed a server had run first.
- **Fix:** `run_scan` calls `init_db` (idempotent) before connecting.
- **Prevention rule:** Every entrypoint that touches the DB must ensure the schema itself. Add a test that runs `run_scan` against a fresh temp DB (tracked in #4).
