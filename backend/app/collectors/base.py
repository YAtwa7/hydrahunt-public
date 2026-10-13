"""Normalized job/lead record shared by all collectors."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


@dataclass
class RawJob:
    source: str  # linkedin | himalayas | greenhouse | reddit | ...
    source_url: str
    company: str
    title: str
    location: str = ""
    description: str = ""
    salary_text: str = ""
    job_type: str = "job"  # job | signal | lead
    posted_at: str | None = None  # ISO date from source
    meta: dict = field(default_factory=dict)

    @property
    def dedup_hash(self) -> str:
        key = f"{_norm(self.company)}|{_norm(self.title)}|{_norm(self.location)}"
        return hashlib.sha1(key.encode()).hexdigest()
