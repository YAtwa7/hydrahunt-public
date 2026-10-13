"""Deterministic scoring — skills, automation preference, boosts/penalties.

Produces rule_score 0-100 and automation_class without spending a token.
LLM scorer later refines only the top of this ranking.
"""

from __future__ import annotations

import re

from app.collectors.base import RawJob
from app.config import AppConfig

SKILL_WEIGHTS = {"core": 3.0, "niche": 4.0, "automation": 2.0, "tools": 1.5, "practices": 1.0}
# practical ceiling: a strong JD hits ~25 weighted skill points; cap there
SCORE_CEILING = 25.0

REQUIRED_RE = re.compile(
    r"(automation|sdet|playwright|selenium|cypress|appium)[^.]{0,80}"
    r"(required|must.have|mandatory|[0-9]+\+?\s*years)",
    re.IGNORECASE,
)
PLUS_RE = re.compile(
    r"(automation|playwright|selenium|cypress)[^.]{0,80}"
    r"(plus|nice to have|bonus|preferred|advantage)",
    re.IGNORECASE,
)
LEARN_RE = re.compile(
    r"(willing to learn|open to learning|exposure to|eagerness to learn)[^.]{0,80}"
    r"(automation|playwright|selenium|cypress|testing tools)"
    r"|(automation|playwright|selenium|cypress)[^.]{0,80}(willing to learn|open to learning)",
    re.IGNORECASE,
)
AUTOMATION_MENTION_RE = re.compile(
    r"\b(test automation|automation engineer|sdet|playwright|selenium|cypress|appium)\b",
    re.IGNORECASE,
)
EMAIL_APPLY_RE = re.compile(
    r"(send (us )?your (cv|resume|résumé)|email (us|your) (at|cv|resume)|"
    r"apply (by|via) email|applications? to\s+\S+@)",
    re.IGNORECASE,
)


def classify_automation(job: RawJob) -> str:
    text = job.description[:6000]
    if LEARN_RE.search(text):
        return "open_to_learn"
    if REQUIRED_RE.search(text):
        return "required"
    if PLUS_RE.search(text):
        return "plus"
    if AUTOMATION_MENTION_RE.search(text):
        return "required"  # mentioned without softener → assume required
    return "none"


AUTO_DOMINANT_RE = re.compile(
    r"\b(automation|automated|sdet|playwright|selenium|cypress|appium|"
    r"test pipeline|testing pipeline)\b",
    re.IGNORECASE,
)
# Manual-first roles are kept even when they mention automation — a manual QA
# seat beside an automation team is exactly the user's profile. A negated
# mention ("manual testing is not part of this role") does NOT count.
MANUAL_FIRST_RE = re.compile(
    r"\bmanual(?:ly)?\s+(?:testing|qa|test cases?|focus(?:ed|ing)?|first)\b"
    r"|\bmanual[- ]first\b|\bmanual(?:ly)?\s+software\s+testing\b",
    re.IGNORECASE,
)
NEGATED_MANUAL_RE = re.compile(
    r"\bmanual\b[^.]{0,40}\b(?:not|no|without)\b"
    r"|\b(?:not|no|without)\b[^.]{0,40}\bmanual\b",
    re.IGNORECASE,
)


def automation_dominant(job: RawJob) -> bool:
    """True when the posting is an automation-first role (hard reject).

    Manual QA roles that merely mention 'automation a plus', offer to teach
    it, or lead with manual testing are NOT dominant — the softener regexes
    (PLUS_RE/LEARN_RE/MANUAL_FIRST_RE) short-circuit before counting."""
    text = job.description[:6000]
    manual_first = bool(MANUAL_FIRST_RE.search(text)) and not NEGATED_MANUAL_RE.search(text)
    if LEARN_RE.search(text) or PLUS_RE.search(text) or manual_first:
        return False
    hits = len(AUTO_DOMINANT_RE.findall(f"{job.title}\n{text}"))
    return hits >= 2


def detect_email_apply(job: RawJob) -> bool:
    return bool(EMAIL_APPLY_RE.search(job.description[:6000]))


def _skill_hits(job_text: str, skills: dict[str, list[str]]) -> float:
    score = 0.0
    for category, weight in SKILL_WEIGHTS.items():
        for skill in skills.get(category, []):
            if re.search(rf"\b{re.escape(skill)}\b", job_text, re.IGNORECASE):
                score += weight
    return score


def score_job(job: RawJob, cfg: AppConfig, location_tag: str, automation_class: str) -> float:
    """0-100 deterministic score."""
    text = f"{job.title}\n{job.description[:6000]}"
    profile = cfg.profile

    raw = _skill_hits(text, profile.get("skills", {}))
    base = min(raw / SCORE_CEILING, 1.0) * 60.0  # skills are worth up to 60 pts

    # automation preference multiplier worth up to 15 pts
    pref = profile.get("automation_preference", {})
    base += float(pref.get(automation_class, 0.5)) * 15.0

    # location feasibility worth up to 15 pts
    loc_pts = {
        "EGYPT_LISTED": 15.0,
        "ANYWHERE": 14.0,
        "EMEA_OK": 11.0,
        "RELOCATION": 10.0,
        "UNKNOWN": 5.0,
        "HYBRID_ONSITE": 2.0,
        "EXCLUDED": 0.0,
    }
    base += loc_pts.get(location_tag, 5.0)

    # boosts/penalties ±2 each, capped ±10
    delta = 0.0
    for boost in profile.get("boosts", []):
        if re.search(rf"\b{re.escape(boost)}\b", text, re.IGNORECASE):
            delta += 2.0
    for penalty in profile.get("penalties", []):
        if re.search(rf"\b{re.escape(penalty)}\b", text, re.IGNORECASE):
            delta -= 4.0
    if detect_email_apply(job):
        delta += 2.0  # hidden-market bonus
    base += max(-10.0, min(10.0, delta))

    # per-company weight (e.g. Testlio gigs deprioritized at 0.4)
    weights = profile.get("company_weights", {})
    company_key = job.company.strip().lower()
    for name, weight in weights.items():
        if name in company_key:
            base *= float(weight)
            break

    return round(max(0.0, min(100.0, base)), 1)


def tier_from_score(score: float, llm_score: float | None = None) -> str:
    final = llm_score if llm_score is not None else score
    if final >= 75:
        return "A"
    if final >= 55:
        return "B"
    return "C"
