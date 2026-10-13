"""Salary estimator v2 — multi-currency with live conversion.

- Parses posted ranges in USD/EUR/GBP/EGP/AED/SAR/CAD/AUD (yearly or monthly)
- Converts everything through FX module (live rates, static fallback)
- Benchmark fallback by seniority × engagement type
- Display: original + USD + EGP equivalents, below-floor flag
- Floor: $1,500/mo USD-equivalent. No maximum.
"""

from __future__ import annotations

import re

from app.collectors.base import RawJob
from app.pipeline.fx import SYMBOL_TO_CODE, get_rates, to_usd

BENCHMARKS = {
    "entry": {"b2b_contractor": (1400, 2400), "eor": (1100, 1900), "full_time_remote": (1000, 1800)},
    "mid": {"b2b_contractor": (2800, 4200), "eor": (2200, 3400), "full_time_remote": (2000, 3200)},
    "senior": {"b2b_contractor": (4200, 6500), "eor": (3400, 5200), "full_time_remote": (3200, 5000)},
    "lead": {"b2b_contractor": (5500, 8500), "eor": (4500, 6800), "full_time_remote": (4200, 6500)},
}
# employment-type scaling on the fallback range (part-time side jobs pay less)
EMPLOYMENT_FACTOR = {"full_time": 1.0, "contract": 1.1, "part_time": 0.55, "freelance": 0.9}
# location factor: relocation/on-site markets pay more; Egypt-listed pays local rates
LOCATION_FACTOR = {
    "ANYWHERE": 1.0, "EMEA_OK": 0.95, "EGYPT_LISTED": 0.8,
    "RELOCATION": 1.15, "HYBRID_ONSITE": 1.05, "UNKNOWN": 0.9,
}
FLOOR_USD = 1500

CUR_CODES = "USD|EUR|GBP|EGP|AED|SAR|CAD|AUD|CHF"
POSTED_SALARY_RE = re.compile(
    rf"(\$|€|£|E£)?\s?(\d{{1,3}}(?:[,.]\d{{3}})+|\d{{4,6}})\s*[-–—to]+\s*"
    rf"(\$|€|£|E£)?\s?(\d{{1,3}}(?:[,.]\d{{3}})+|\d{{4,6}})\s*(k|K)?\s*"
    rf"(per year|annually|yearly|/yr|per month|monthly|/mo|{CUR_CODES})?",
    re.IGNORECASE,
)
MONTHLY_RE = re.compile(r"per month|monthly|/mo\b|/month|a month|USD/month", re.IGNORECASE)
CUR_CODE_RE = re.compile(rf"\b({CUR_CODES})\b", re.IGNORECASE)


def _seniority(job: RawJob) -> str:
    text = f"{job.title} {job.description[:2000]}".lower()
    if re.search(r"\b(lead|staff|principal|head of)\b", text):
        return "lead"
    if re.search(r"\b(senior|sr\.?)\b", text):
        return "senior"
    if re.search(r"\b(junior|jr\.?|entry.?level|intern(ship)?|graduate)\b", text):
        return "entry"
    return "mid"


def _num(s: str) -> float:
    """'4,000' / '48.000' (thousands separators) or plain '48000' → float."""
    if re.fullmatch(r"\d{1,3}([,.]\d{3})+", s):
        return float(re.sub(r"[,.]", "", s))
    return float(s)


def _detect_currency(symbol: str | None, context: str) -> str:
    if symbol:
        return SYMBOL_TO_CODE.get(symbol, "USD")
    m = CUR_CODE_RE.search(context)
    return m.group(1).upper() if m else "USD"


def _parse_posted(salary_text: str, description: str, rates: dict[str, float]) -> dict | None:
    for text in (salary_text, description[:3000]):
        if not text:
            continue
        m = POSTED_SALARY_RE.search(text)
        if not m:
            continue
        cur = _detect_currency(m.group(1), text[m.end():m.end() + 20])
        lo, hi = _num(m.group(2)), _num(m.group(4))
        if lo < 500 and not MONTHLY_RE.search(m.group(0)):
            lo, hi = lo * 1000, hi * 1000
        if not MONTHLY_RE.search(m.group(0)):
            lo, hi = lo / 12, hi / 12
        usd_lo, usd_hi = to_usd(lo, cur, rates), to_usd(hi, cur, rates)
        if 300 < usd_lo < 50_000:
            return {"min": usd_lo, "max": usd_hi, "cur": cur, "orig_min": lo, "orig_max": hi,
                    "kind": "posted", "confidence": "high"}
    return None


def estimate(
    job: RawJob, location_tag: str, profile: dict, employment_type: str = "full_time"
) -> dict:
    """Monthly estimate dict with USD values + multi-currency display."""
    rates = get_rates()
    floor = float(profile.get("salary", {}).get("floor_usd", FLOOR_USD))

    posted = _parse_posted(job.salary_text, job.description, rates)
    if posted:
        usd_lo, usd_hi = posted["min"], posted["max"]
        kind, conf = "posted", "high"
        orig = f"{posted['cur']} {int(posted['orig_min']):,}–{int(posted['orig_max']):,}"
    else:
        seniority = _seniority(job)
        engagement = (
            "b2b_contractor"
            if employment_type in ("contract", "freelance")
            or "contractor" in job.description[:2000].lower()
            else "full_time_remote"
        )
        lo, hi = BENCHMARKS[seniority][engagement]
        factor = EMPLOYMENT_FACTOR.get(employment_type, 1.0) * LOCATION_FACTOR.get(
            location_tag, 0.9
        )
        usd_lo, usd_hi = lo * factor, hi * factor
        kind = f"est. {engagement.replace('_', ' ')}"
        conf = "medium" if location_tag in ("ANYWHERE", "EMEA_OK") else "low"
        orig = ""

    egp_lo, egp_hi = int(usd_lo * rates.get("EGP", 48.5)), int(usd_hi * rates.get("EGP", 48.5))
    below_floor = usd_hi < floor           # entire range under the floor
    crosses_floor = usd_lo < floor <= usd_hi  # range straddles the floor
    display = f"${int(usd_lo):,}–{int(usd_hi):,} · E£{egp_lo:,}–{egp_hi:,}/mo"
    display = f"{orig} (≈{display}) {kind}" if orig else f"{display} {kind}"
    if below_floor:
        display = f"⚠️ below floor · {display}"
    elif crosses_floor:
        display = f"△ floor edge · {display}"

    return {
        "min": int(usd_lo),
        "max": int(usd_hi),
        "currency": "USD",
        "kind": kind,
        "confidence": conf,
        "below_floor": below_floor,
        "display": display,
    }
