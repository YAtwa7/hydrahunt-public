"""ATS public API collector — Greenhouse, Lever, Ashby, SmartRecruiters, Breezy, Recruitee.

Polls target companies from companies.yml directly through their
public job-board APIs. No scraping, no auth, most reliable source.
"""

from __future__ import annotations

import logging
import re

import httpx

from app.collectors.base import RawJob

log = logging.getLogger("jobhunt.ats")

TIMEOUT = httpx.Timeout(20.0)
HEADERS = {"User-Agent": "hydrahunt/0.1 (personal job search tool)"}


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")[:8000]


def _greenhouse(token: str) -> list[dict]:
    resp = httpx.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        params={"content": "true"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for j in resp.json().get("jobs", []):
        out.append(
            {
                "title": j.get("title", ""),
                "url": j.get("absolute_url", ""),
                "location": (j.get("location") or {}).get("name", ""),
                "description": _strip(j.get("content", "")),
                "posted_at": j.get("updated_at"),
            }
        )
    return out


def _lever(token: str) -> list[dict]:
    resp = httpx.get(
        f"https://api.lever.co/v0/postings/{token}",
        params={"mode": "json"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for j in resp.json():
        cats = j.get("categories") or {}
        out.append(
            {
                "title": j.get("text", ""),
                "url": j.get("hostedUrl", ""),
                "location": cats.get("location", ""),
                "description": _strip(j.get("descriptionPlain", "") or j.get("description", "")),
                "posted_at": j.get("createdAt"),
            }
        )
    return out


def _ashby(token: str) -> list[dict]:
    resp = httpx.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{token}",
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for j in resp.json().get("jobs", []):
        out.append(
            {
                "title": j.get("title", ""),
                "url": j.get("jobUrl", ""),
                "location": (j.get("location") or {}).get("name", "")
                if isinstance(j.get("location"), dict)
                else str(j.get("location", "")),
                "description": _strip(j.get("descriptionHtml", "")),
                "posted_at": j.get("publishedAt"),
            }
        )
    return out


def _smartrecruiters(token: str) -> list[dict]:
    resp = httpx.get(
        f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
        params={"limit": 100},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    out = []
    for j in resp.json().get("content", []):
        loc = j.get("location") or {}
        out.append(
            {
                "title": j.get("name", ""),
                "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id', '')}",
                "location": ", ".join(p for p in (loc.get("city"), loc.get("country")) if p),
                "description": "",
                "posted_at": j.get("releasedDate"),
            }
        )
    return out


def _breezy(token: str) -> list[dict]:
    resp = httpx.get(f"https://{token}.breezy.hr/json", headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    out = []
    for j in resp.json():
        loc = j.get("location") or {}
        out.append(
            {
                "title": j.get("name", ""),
                "url": j.get("url", ""),
                "location": loc.get("name", ""),
                "description": _strip(j.get("description", "")),
                "posted_at": j.get("published_date"),
            }
        )
    return out


def _recruitee(token: str) -> list[dict]:
    resp = httpx.get(
        f"https://{token}.recruitee.com/api/offers", headers=HEADERS, timeout=TIMEOUT
    )
    resp.raise_for_status()
    out = []
    for j in resp.json().get("offers", []):
        out.append(
            {
                "title": j.get("title", ""),
                "url": j.get("careers_url", ""),
                "location": j.get("location", ""),
                "description": _strip(j.get("description", "")),
                "posted_at": j.get("published_at"),
            }
        )
    return out


ATS_FETCHERS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
    "breezy": _breezy,
    "recruitee": _recruitee,
}


def collect(cfg: dict, companies: dict) -> list[RawJob]:
    if not cfg.get("collectors", {}).get("ats_companies", {}).get("enabled"):
        return []
    blacklist = {c.lower() for c in companies.get("blacklist", [])}
    jobs: list[RawJob] = []

    for company in companies.get("companies", []):
        if not company.get("enabled", True):
            continue
        name = str(company.get("name", ""))
        ats = str(company.get("ats", "")).lower()
        token = str(company.get("board_token", ""))
        if name.lower() in blacklist or ats not in ATS_FETCHERS or not token:
            continue
        try:
            postings = ATS_FETCHERS[ats](token)
        except Exception:
            log.warning("ATS fetch failed for %s (%s)", name, ats, exc_info=True)
            continue
        for p in postings:
            jobs.append(
                RawJob(
                    source=f"ats:{ats}",
                    source_url=p["url"],
                    company=name,
                    title=p["title"],
                    location=p["location"],
                    description=p["description"],
                    posted_at=str(p["posted_at"]) if p.get("posted_at") else None,
                )
            )
        log.info("%s: %d postings", name, len(postings))
    return jobs
