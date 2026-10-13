"""Ask HydraHunt - grounded Q&A about this codebase.

Reuses the free-model fallback chain in `app.scoring.llm` so the answer step
costs nothing extra and degrades identically: no API keys configured means a
clear error, not a hang.

Grounding is `docs/REPO_BRIEF.md` plus a live snapshot of the running system
(version, enabled collectors). Answers are never persisted.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from app.config import AppConfig
from app.scoring.llm import _call_provider, _last_good, _provider_keys

ROOT = Path(__file__).resolve().parents[2]
BRIEF_PATH = ROOT / "docs" / "REPO_BRIEF.md"

MAX_QUESTION_CHARS = 500
DEFAULT_MAX_CONTEXT_CHARS = 14_000

# In-process rate limit. The server binds 127.0.0.1 by default, so this is
# defence in depth rather than the primary control - but a forker who rebinds
# app.host should not accidentally expose an open LLM proxy.
_RATE_LOCK = threading.Lock()
_calls: list[float] = []


def _rate_ok(limit: int, window_s: int) -> bool:
    """Sliding-window limiter. Returns False when the caller is over budget."""
    if limit <= 0:
        return True
    now = time.monotonic()
    with _RATE_LOCK:
        _calls[:] = [t for t in _calls if now - t < window_s]
        if len(_calls) >= limit:
            return False
        _calls.append(now)
        return True


def _load_brief(limit: int) -> str:
    """Read the grounding brief, truncated to `limit` characters."""
    try:
        text = BRIEF_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    if limit > 0 and len(text) > limit:
        # Cut on a section boundary when we can, so we never end mid-word.
        cut = text.rfind("\n## ", 0, limit)
        text = text[: cut if cut > limit // 2 else limit]
    return text


def _live_state(cfg: AppConfig) -> str:
    """A short factual snapshot of the running system."""
    collectors = cfg.raw.get("collectors", {}) or {}
    enabled = sorted(
        name for name, block in collectors.items() if (block or {}).get("enabled")
    )
    companies = cfg.companies.get("companies", []) or []
    return (
        "Live state of this running instance:\n"
        f"- enabled collectors: {', '.join(enabled) or 'none'}\n"
        f"- companies in the ATS registry: {len(companies)}\n"
        f"- db: {cfg.db_path}\n"
        f"- scheduler enabled: {bool((cfg.raw.get('scheduler') or {}).get('enabled'))}\n"
        f"- ask endpoint enabled: {bool((cfg.raw.get('ask') or {}).get('enabled'))}\n"
    )


SYSTEM_PROMPT = """You are the built-in architecture guide for the HydraHunt \
codebase. You answer questions about how this repository works.

ABSOLUTE RULES:
1. Answer ONLY from the briefing below. It is the ground truth for this repo.
2. NEVER invent identifiers. Every tag name, regex name, constant, file path,
   endpoint and function you mention MUST appear verbatim in the briefing. A
   made-up name is worse than saying you don't know.
3. The ONLY location tags that exist are exactly: ANYWHERE, EMEA_OK,
   EGYPT_LISTED, RELOCATION, HYBRID_ONSITE, EXCLUDED, UNKNOWN. There are no
   others - there is no BRAZIL_LISTED, no INDIA_LISTED, no per-country tags.
4. If the briefing does not answer the question, reply with exactly:
   "Not covered in the briefing. The closest file is <path>." then stop.
   Do not guess, do not extrapolate, do not fill the gap from general knowledge.
5. Do not describe cascade order, precedence or control flow unless the briefing
   states it explicitly.

STYLE:
- Lead with the direct answer in one or two sentences.
- Then at most 5 bullets of specifics, each citing a real path like
  `pipeline/location.py` or a real function name.
- Close with the file path to read.
- No preamble, no restating the question, under 200 words.

Respond with STRICT JSON only, matching exactly:
{"answer": "<your answer, as a JSON string>"}
"""


def ask(question: str, cfg: AppConfig) -> dict[str, Any]:
    """Answer a question about the codebase. Never raises.

    Returns `{"answer": str, "model": str}` on success, or
    `{"error": str}` when disabled, rate-limited, unconfigured or unsuccessful.
    """
    question = (question or "").strip()
    if not question:
        return {"error": "question is required"}
    if len(question) > MAX_QUESTION_CHARS:
        return {
            "error": f"question too long (max {MAX_QUESTION_CHARS} characters)"
        }

    acfg = cfg.raw.get("ask") or {}
    if not acfg.get("enabled", True):
        return {"error": "ask endpoint is disabled (set ask.enabled in config.yml)"}

    if not _rate_ok(
        int(acfg.get("rate_limit_calls", 20)),
        int(acfg.get("rate_limit_window_s", 3600)),
    ):
        return {"error": "ask rate limit reached - try again later"}

    context = _load_brief(int(acfg.get("max_context_chars", DEFAULT_MAX_CONTEXT_CHARS)))
    if not context:
        return {"error": f"grounding brief missing at {BRIEF_PATH}"}

    prompt = (
        f"{context}\n\n---\nLIVE INSTANCE STATE ---\n{_live_state(cfg)}\n\n"
        f"---\nQUESTION ---\n{question}"
    )

    keys = _provider_keys(cfg)
    max_models = int(acfg.get("max_models", 3))
    for provider in cfg.raw.get("llm", {}).get("providers", []):
        api_key = keys.get(provider.get("name", ""), "")
        if not api_key:
            continue
        pname = provider.get("name", "")
        models = list(provider.get("models", []))
        # Same last-known-good memo the scorer uses. Free models are slow and
        # flaky; if one already answered this process, try it first instead of
        # burning a 60s timeout on dead models ahead of it in the list.
        good = _last_good.get(pname)
        if good in models:
            models.remove(good)
            models.insert(0, good)

        for model in models[:max_models]:
            result = _call_provider(
                provider, api_key, model, prompt, system=SYSTEM_PROMPT
            )
            if not isinstance(result, dict):
                continue
            answer = result.get("answer")
            if isinstance(answer, str) and answer.strip():
                _last_good[pname] = model
                return {"answer": answer.strip(), "model": model}

    return {
        "error": "no LLM provider answered - configure an API key in .env "
        "or refresh models with scripts/update_models.py"
    }
