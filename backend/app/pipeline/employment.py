"""Employment type + timezone fit detection.

employment_type: full_time | part_time | contract | freelance
timezone_fit: americas | emea | apac | flexible | unknown
Both are informational chips + light scoring signals (part-time welcome).
"""

from __future__ import annotations

import re

from app.collectors.base import RawJob

PART_TIME_RE = re.compile(r"\b(part.?time|20\s*(h|hours)/week|half.?time)\b", re.IGNORECASE)
CONTRACT_RE = re.compile(
    r"\b(contract(or)?|b2b|freelance|1099|independent contractor)\b", re.IGNORECASE
)
FREELANCE_RE = re.compile(r"\b(freelance|gig|per.?project)\b", re.IGNORECASE)
FULL_TIME_RE = re.compile(r"\b(full.?time|permanent)\b", re.IGNORECASE)

AMERICAS_TZ_RE = re.compile(
    r"\b(est|edt|pst|pdt|cst|cdt|mst|mdt|eastern time|pacific time|"
    r"us time(zone)?s?|americas? time(zone)?|north america(n)? hours)\b",
    re.IGNORECASE,
)
EMEA_TZ_RE = re.compile(
    r"\b(cet|cest|gmt|bst|eet|european? time(zone)?|emea time(zone)?)\b", re.IGNORECASE
)
APAC_TZ_RE = re.compile(
    r"\b(ist|jst|kst|aest|sgt|hkt|india time|apac|asia time(zone)?)\b", re.IGNORECASE
)


def detect_employment_type(job: RawJob) -> str:
    text = f"{job.title} {job.description[:3000]}"
    if FREELANCE_RE.search(text):
        return "freelance"
    if PART_TIME_RE.search(text):
        return "part_time"
    if CONTRACT_RE.search(text):
        return "contract"
    if FULL_TIME_RE.search(text):
        return "full_time"
    return "full_time"  # default assumption


def detect_timezone_fit(job: RawJob) -> str:
    text = f"{job.title} {job.location} {job.description[:3000]}"
    hits = set()
    if AMERICAS_TZ_RE.search(text):
        hits.add("americas")
    if EMEA_TZ_RE.search(text):
        hits.add("emea")
    if APAC_TZ_RE.search(text):
        hits.add("apac")
    if not hits:
        return "flexible"  # no tz requirement stated = async/flexible
    if len(hits) > 1:
        return "flexible"
    return hits.pop()
