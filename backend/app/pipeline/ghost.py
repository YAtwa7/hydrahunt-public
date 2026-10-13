"""Ghost-job & repost detection.

Ghost jobs = postings companies never intend to fill (evergreen req
collectors, resume farmers, headcount theater). Signals:

- posting age > 45 days and still open
- reposted repeatedly (we've seen it N times over weeks)
- evergreen phrasing ("future opportunities", "talent pool", "ongoing")
- vague description, no salary, no real requirements
- title+company seen across many locations (spray posting)

Score 0-100 (clamped). Penalties start at >=30 (see tier_penalty);
>=50 is what the dashboard surfaces as a chip. Shown as 👻 chip + score penalty.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

EVERGREEN_RE = re.compile(
    r"\b(future (roles|opportunities|openings)|talent pool|evergreen|"
    r"ongoing (hiring|recruitment)|always hiring|general application|"
    r"expression of interest|join our talent (community|network)|pipeline role)\b",
    re.IGNORECASE,
)
VAGUE_RE = re.compile(
    r"\b(various (locations|roles)|multiple positions|competitive salary|"
    r"to be discussed|duties as assigned)\b",
    re.IGNORECASE,
)


def _age_days(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return max(0, (datetime.now(UTC) - d).days)
    except ValueError:
        return None


def compute_ghost_score(
    *,
    posted_at: str | None,
    times_seen: int,
    description: str,
    salary_text: str,
    location: str,
) -> tuple[int, list[str]]:
    """Return (0-100 ghost score, list of triggered signals)."""
    score = 0
    signals: list[str] = []

    age = _age_days(posted_at)
    if age is not None:
        if age > 60:
            score += 35
            signals.append(f"posted {age}d ago, still open")
        elif age > 45:
            score += 20
            signals.append(f"aging posting ({age}d)")

    if times_seen >= 3:
        score += 25
        signals.append(f"reposted {times_seen}x")
    elif times_seen == 2:
        score += 10
        signals.append("reposted once")

    if EVERGREEN_RE.search(description[:3000]) or EVERGREEN_RE.search(salary_text):
        score += 30
        signals.append("evergreen/talent-pool phrasing")

    vague_hits = len(VAGUE_RE.findall(description[:3000]))
    if vague_hits >= 2:
        score += 15
        signals.append("vague description")
    elif vague_hits == 1:
        score += 7

    if not salary_text.strip():
        score += 5  # no salary transparency, weak signal alone

    if len(description.strip()) < 300:
        score += 10
        signals.append("suspiciously thin description")

    if location.count(",") >= 4 or "multiple locations" in location.lower():
        score += 10
        signals.append("spray-posted across locations")

    return min(score, 100), signals


def tier_penalty(ghost_score: int) -> float:
    """Points subtracted from rule score."""
    if ghost_score >= 70:
        return 12.0
    if ghost_score >= 50:
        return 7.0
    if ghost_score >= 30:
        return 3.0
    return 0.0
