"""LLM scorer — free-model fallback chain (OpenRouter → Agnes).

Scores ONLY the top rule-ranked jobs (config: llm.max_jobs_per_scan).
Strict JSON output with validation; any provider/model failure falls
through the chain; total failure leaves llm_score=None and the
pipeline continues on rule_score alone.

No API keys configured → scorer no-ops cleanly.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.collectors.base import RawJob
from app.config import AppConfig

log = logging.getLogger("jobhunt.llm")

SYSTEM_PROMPT = """You are a job-fit analyst. Score this job for the candidate below.
Return STRICT JSON only:
{"score": 0-100, "tier": "A"|"B"|"C",
 "automation": "none"|"plus"|"open_to_learn"|"required",
 "location_fit": "ANYWHERE"|"EMEA_OK"|"EGYPT_LISTED"|"RELOCATION"|"HYBRID_ONSITE"|"EXCLUDED",
 "gaps": ["..."], "pitch": "one sentence why this fits",
 "reasoning": "2-3 sentences max"}

Tier guide: A = strong match, apply now. B = decent match, worth reviewing. C = weak/skip.
Candidate preference: manual-first QA roles; automation as a plus is fine; heavy SDET-only roles score lower.
Location: candidate lives in Egypt (UTC+2). Anywhere-remote, EMEA-remote, EOR/contractor-friendly,
relocation offers all work. US-only or excluded regions = EXCLUDED."""


def _candidate_summary(cfg: AppConfig) -> str:
    p = cfg.profile
    skills = p.get("skills", {})
    flat = "; ".join(f"{k}: {', '.join(v[:8])}" for k, v in skills.items())
    return (
        f"Headline: {p.get('identity', {}).get('headline', '')}\n"
        f"Years: {p.get('identity', {}).get('years_experience', '?')}, "
        f"seniority: {p.get('identity', {}).get('seniority_band', [])}\n"
        f"Skills — {flat}"
    )


def _provider_keys(cfg: AppConfig) -> dict[str, str]:
    return {
        "openrouter": cfg.settings.openrouter_api_key,
        "agnes": cfg.settings.agnes_api_key,
    }


def _call_provider(
    provider: dict[str, Any],
    api_key: str,
    model: str,
    prompt: str,
    system: str | None = None,
) -> dict[str, Any] | None:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = httpx.post(
            f"{provider['base_url']}/chat/completions",
            headers=headers,
            json=body,
            timeout=provider.get("timeout_s", 60),
        )
        if resp.status_code >= 400:
            log.warning("%s/%s → HTTP %s", provider["name"], model, resp.status_code)
            return None
        content = resp.json()["choices"][0]["message"].get("content")
        if not content:
            # free/stealth models sometimes return null content (refusal, reasoning-only)
            log.warning("%s/%s returned empty content", provider["name"], model)
            return None
        return json.loads(content)
    except Exception:  # noqa: BLE001 — any single-model failure must fall through the chain
        log.warning("%s/%s call failed", provider["name"], model, exc_info=True)
        return None


# first-working-model memo: free models rotate/die mid-week; once one answers,
# it goes first for the rest of the process (no re-punching dead doors × 20 jobs)
_last_good: dict[str, str] = {}


def score_with_llm(job: RawJob, cfg: AppConfig) -> dict[str, Any] | None:
    """Try provider models in order (last known-good first). Returns verdict or None."""
    keys = _provider_keys(cfg)
    providers = cfg.raw.get("llm", {}).get("providers", [])
    prompt = (
        f"CANDIDATE:\n{_candidate_summary(cfg)}\n\n"
        f"JOB:\nTitle: {job.title}\nCompany: {job.company}\n"
        f"Location: {job.location}\nPosted salary: {job.salary_text or 'not listed'}\n"
        f"Description (truncated):\n{job.description[:5000]}"
    )
    for provider in providers:
        name = provider.get("name", "")
        api_key = keys.get(name, "")
        if not api_key:
            continue
        models = list(provider.get("models", []))
        good = _last_good.get(name)
        if good in models:
            models.remove(good)
            models.insert(0, good)
        for model in models:
            result = _call_provider(provider, api_key, model, prompt)
            if result and isinstance(result.get("score"), int | float):
                _last_good[name] = model
                result["_model"] = model
                return result
    return None
