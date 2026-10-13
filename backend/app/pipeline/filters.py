"""Hard filters — title match, blocklist, excluded regions, company blacklist.

All deterministic, zero tokens. Runs before any scoring.
"""

from __future__ import annotations

import re

from app.collectors.base import RawJob
from app.config import AppConfig
from app.pipeline.rules_score import automation_dominant


def _build_title_pattern(titles: list[str]) -> re.Pattern:
    parts = [re.escape(t) for t in titles if t]
    return re.compile("|".join(parts), re.IGNORECASE) if parts else re.compile(r"$^")


YEARS_REQ_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?(?:proven\s+|relevant\s+|"
    r"professional\s+|hands.?on\s+|work\s+)?experience"
    r"|minimum\s+(?:of\s+)?(\d{1,2})\s*\+?\s*years?"
    r"|(\d{1,2})\s*(?:–|-|to)\s*\d{1,2}\s*years?\s+(?:of\s+)?experience"
    r"|experience\s+of\s+(\d{1,2})\s*\+?\s*years?",
    re.IGNORECASE,
)

# Compliance/audit-style "assurance" roles (banking 2nd-line assurance,
# control assurance, regulatory testing) are NOT software QA — nothing to do
# with the SDLC. Deliberately narrow: "quality assurance over the SDLC" and
# ordinary "compliance testing on our web app" must NOT match.
COMPLIANCE_ASSURANCE_RE = re.compile(
    r"\b(?:2nd|second)[ -]line\s+assurance|control assurance|regulatory assurance|"
    r"regulatory affairs|regulatory compliance testing|"
    r"(?:gmp|glp|gcp)[ -]compliance|iso\s*13485|21\s*cfr\s*part\s*11",
    re.IGNORECASE,
)

# People-manager roles (the candidate is an IC: mid→Senior manual QA).
# Covers both "QA Manager"/"Quality Engineering Manager" and the
# "Sr Manager, QA Engineering" comma form.
MANAGER_RE = re.compile(
    r"\b(?:sr\.?\s*|senior\s+)?(?:qa|quality|test)\s+"
    r"(?:(?:assurance|program|engineering|delivery)\s+)?manager\b"
    r"|\bmanager[,\s]+(?:of\s+)?(?:qa|quality|test|quality assurance)\b",
    re.IGNORECASE,
)


class HardFilter:
    def __init__(self, cfg: AppConfig) -> None:
        self.title_re = _build_title_pattern(cfg.target_titles)
        # generic QA tokens catch variants like "QA Automation Engineer"
        # that don't contain exact target phrases
        self.title_generic_re = re.compile(
            r"\b(qa|sdet|quality assurance|quality engineer|test engineer|"
            r"test|software tester|test automation|ai quality|llm eval)\b",
            re.IGNORECASE,
        )
        self.blocklist_re = _build_title_pattern(cfg.title_blocklist)
        self.excluded = cfg.excluded_regions
        self.company_blacklist = cfg.company_blacklist
        self.company_us_only = cfg.company_us_only
        self.max_years = int(cfg.profile.get("identity", {}).get("max_required_years", 99))
        # QA keyword safety net for leads (reddit/HN posts have free-form titles)
        self.qa_net = re.compile(
            r"\b(qa|quality assurance|sdet|testing|test engineer|software tester)\b",
            re.IGNORECASE,
        )

    def _years_over_limit(self, job: RawJob) -> int | None:
        """Highest years-of-experience requirement found, or None."""
        found = [
            int(g)
            for m in YEARS_REQ_RE.finditer(f"{job.title} {job.description[:3000]}")
            for g in m.groups()
            if g
        ]
        worst = max(found) if found else 0
        return worst if worst > self.max_years else None

    def _region_hit(self, job: RawJob) -> str | None:
        hay = f"{job.location} {job.description[:2000]}".lower()
        for region in self.excluded:
            # word-boundary: "india" must not hit "Indianapolis"
            if re.search(rf"\b{re.escape(region)}\b", job.location.lower()) or re.search(
                rf"(based in|located in|location:?\s*|must (?:be|reside) in)\s*{re.escape(region)}\b",
                hay,
            ):
                return region
        return None

    def _us_only_employer(self, job: RawJob) -> str | None:
        """US-only employer (user rule: 'remote from USA is BS'): companies that
        hire within the US / are clearance-gated never reach the pool, even when
        the posting says only 'Remote'."""
        c = (job.company or "").strip().lower()
        if len(c) < 3:  # fixtures/abbreviations must never substring-match
            return None
        for name in self.company_us_only:
            if len(name) >= 3 and (name in c or c in name):
                return name
        return None

    def reject_reason(self, job: RawJob) -> str | None:
        """None = passes. String = why it was rejected."""
        if not job.title.strip():
            return "empty title"
        if job.company and job.company.lower() in self.company_blacklist:
            return f"blacklisted company: {job.company}"
        if self.blocklist_re.search(job.title):
            return "title blocklist"
        us_only = self._us_only_employer(job)
        if us_only:
            return f"US-only employer: {us_only}"
        region = self._region_hit(job)
        if region:
            return f"excluded region: {region}"
        if MANAGER_RE.search(job.title):
            return "people-manager role"
        if COMPLIANCE_ASSURANCE_RE.search(f"{job.title}\n{job.description[:2000]}"):
            return "compliance-assurance role"
        if automation_dominant(job):
            return "automation-focused role"
        years = self._years_over_limit(job)
        if years:
            return f"requires {years}y experience (limit {self.max_years}y)"
        # jobs need a target-title match; leads get the QA safety net
        if job.job_type == "job":
            if not (self.title_re.search(job.title) or self.title_generic_re.search(job.title)):
                return "title not targeted"
        elif not (self.title_re.search(job.title) or self.qa_net.search(job.title)):
            return "lead not QA-related"
        return None
