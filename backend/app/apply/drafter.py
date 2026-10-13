"""Draft generation via the free LLM chain.

Evidence-bounded: the prompt carries the full candidate profile and an
explicit anti-fabrication instruction. Any LLM/parse failure returns None
(drafting is a convenience, never a blocker).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from app.config import AppConfig
from app.scoring.llm import _call_provider, _provider_keys

log = logging.getLogger("hydrahunt.apply")

SYSTEM_PROMPT = """You are an application-writing assistant for a QA engineer.
You write paste-ready application material grounded ONLY in the candidate profile provided.

HARD RULES (violations make the output useless):
1. NEVER invent employers, job titles, years of experience, metrics, degrees, or skills
   that are not explicitly in the candidate profile.
2. If the job asks about something not in the profile, write:
   "No direct experience, but <closest REAL evidence from the profile>".
3. Use the profile's real achievements with their real numbers — do not embellish them.
4. Tone: direct, confident, no fluff, no buzzword soup. Short sentences.
5. Return STRICT JSON only, matching the requested schema exactly."""

SCHEMA_HINT = """Return STRICT JSON:
{"summary": "2-3 sentence professional summary tailored to the role",
 "why_this_role": "why this company/role specifically (reference JD facts)",
 "why_you": "why the candidate fits (profile evidence only)",
 "experience_answers": [{"question": "...", "answer": "..."}],
 "cover_email": "Subject: ...\\n\\n<body>",
 "gaps": ["honest gaps between JD and profile"]}
Include 4-6 experience_answers for the most common application questions
(tell us about yourself, your core skills and competencies in QA, why this
company, a challenging bug/project, experience with the JD's top requirement,
remote work experience). Always include the "core skills and competencies in
QA" question for a hands-on QA role."""


def _profile_text(cfg: AppConfig) -> str:
    p = cfg.profile
    skills = p.get("skills", {})
    flat = "\n".join(f"  {k}: {', '.join(v)}" for k, v in skills.items())
    ident = p.get("identity", {})
    sal = p.get("salary", {})
    answers = p.get("answers", [])
    ans_block = "\n".join(
        f"Q: {a.get('question', '')}\nA: {a.get('answer', '')}\n" for a in answers
    )
    return (
        f"CANDIDATE PROFILE (the ONLY source of truth):\n"
        f"Headline: {ident.get('headline', '')}\n"
        f"Years experience: {ident.get('years_experience', '?')} "
        f"(seniority: {', '.join(ident.get('seniority_band', []))})\n"
        f"Location: {ident.get('location', '')} "
        f"(open to relocation: {ident.get('open_to_relocation')})\n"
        f"Languages: "
        + ", ".join(
            lang.get("lang", "") + " " + str(lang.get("level", ""))
            for lang in p.get("languages", [])
        )
        + "\n"
        f"Skills:\n{flat}\n"
        f"Previous salary: ${sal.get('previous_usd', '?')}/mo; "
        f"target: ${sal.get('target_min_usd', '?')}+/mo\n"
        f"Real achievements (from CV profile — the only citable facts):\n"
        + "\n".join(f"- {a}" for a in p.get("achievements", []))
        + "\n"
        f"PRE-WRITTEN CANONICAL ANSWERS (reuse these facts; adapt the wording to "
        f"the role — never add new facts):\n{ans_block}"
    )


def generate_draft(job: dict[str, Any], cfg: AppConfig) -> dict[str, Any] | None:
    """Generate an application draft for a job row. None on any failure."""
    prompt = (
        f"{_profile_text(cfg)}\n\n"
        f"JOB:\nTitle: {job.get('title', '')}\nCompany: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Salary info: {job.get('salary_text') or job.get('salary_estimate') or 'not listed'}\n"
        f"Description (truncated):\n{str(job.get('description', ''))[:5000]}\n\n"
        f"{SCHEMA_HINT}"
    )
    keys = _provider_keys(cfg)
    for provider in cfg.raw.get("llm", {}).get("providers", []):
        api_key = keys.get(provider.get("name", ""), "")
        if not api_key:
            continue
        for model in provider.get("models", []):
            result = _call_provider(provider, api_key, model, prompt, system=SYSTEM_PROMPT)
            if result and isinstance(result.get("summary"), str):
                result["_model"] = model
                return result
    log.warning("draft generation failed for job %s", job.get("id"))
    return None


def get_cached(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT draft_json, model FROM drafts WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    try:
        draft = json.loads(row["draft_json"])
        draft["_model"] = row["model"]
        return draft
    except json.JSONDecodeError:
        return None


def save_draft(conn: sqlite3.Connection, job_id: int, draft: dict[str, Any]) -> None:
    model = str(draft.get("_model", ""))
    conn.execute(
        """INSERT INTO drafts (job_id, draft_json, model) VALUES (?,?,?)
           ON CONFLICT(job_id) DO UPDATE SET
             draft_json=excluded.draft_json, model=excluded.model,
             created_at=datetime('now')""",
        (job_id, json.dumps(draft), model),
    )
    conn.commit()
