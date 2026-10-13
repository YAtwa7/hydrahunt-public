"""Shared live scan progress — single-process, thread-safe.

The scan worker thread writes; the API reads. In-memory only,
rebuilt every scan. Powers the dashboard scan bar.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

_lock = threading.Lock()
_state: dict = {
    "phase": "idle",
    "collectors": {},  # name -> {"status": running|done|failed, "count": int}
    "jobs_found": 0,
    "filtered": 0,
    "scored": 0,
    "llm_done": 0,
    "llm_total": 0,
    "started_at": None,
}


def reset(collector_names: list[str]) -> None:
    with _lock:
        _state.update(
            phase="collecting",
            collectors={n: {"status": "running", "count": 0} for n in collector_names},
            jobs_found=0,
            filtered=0,
            scored=0,
            llm_done=0,
            llm_total=0,
            started_at=datetime.now(UTC).isoformat(),
        )


def set_phase(phase: str) -> None:
    with _lock:
        _state["phase"] = phase


def collector_done(name: str, count: int) -> None:
    with _lock:
        if name in _state["collectors"]:
            _state["collectors"][name] = {"status": "done", "count": count}
        _state["jobs_found"] += count


def collector_failed(name: str) -> None:
    with _lock:
        if name in _state["collectors"]:
            _state["collectors"][name]["status"] = "failed"


def set_filtered(n: int) -> None:
    with _lock:
        _state["filtered"] = n


def set_scored(n: int) -> None:
    with _lock:
        _state["scored"] = n


def set_llm_total(n: int) -> None:
    with _lock:
        _state["llm_total"] = n


def inc_llm_done() -> None:
    with _lock:
        _state["llm_done"] += 1


def snapshot() -> dict:
    with _lock:
        return {
            "phase": _state["phase"],
            "collectors": dict(_state["collectors"]),
            "jobs_found": _state["jobs_found"],
            "filtered": _state["filtered"],
            "scored": _state["scored"],
            "llm_done": _state["llm_done"],
            "llm_total": _state["llm_total"],
            "started_at": _state["started_at"],
        }
