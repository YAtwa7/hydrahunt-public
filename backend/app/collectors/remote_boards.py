"""Remote job boards — all free, no-auth JSON APIs and RSS feeds.

Himalayas (RSS), Remotive (JSON API), RemoteOK (JSON API),
WeWorkRemotely (RSS), WorkingNomads (RSS).
"""

from __future__ import annotations

import logging
import re

import feedparser
import httpx

from app.collectors.base import RawJob

log = logging.getLogger("jobhunt.remote_boards")

TIMEOUT = httpx.Timeout(30.0)
HEADERS = {"User-Agent": "hydrahunt/0.1 (personal job search tool)"}

QA_PATTERN = re.compile(
    r"\b(qa|quality assurance|quality engineer|sdet|test engineer|testing|"
    r"software tester|qa automation|ai quality|llm eval)\b",
    re.IGNORECASE,
)


def _is_qa(title: str, description: str = "") -> bool:
    return bool(QA_PATTERN.search(title))


def _split_company_title(title: str) -> tuple[str, str]:
    """Feeds often pack 'Company: Role' or 'Role @ Company' into the title."""
    for sep in (": ", " @ ", " - ", " – "):
        if sep in title:
            left, right = title.split(sep, 1)
            # heuristic: company is usually the shorter side
            company, role = (left, right) if len(left) <= len(right) else (right, left)
            return company.strip(), role.strip()
    return "", title.strip()


def _company_from_url(url: str) -> str:
    """himalayas.app/companies/<slug>/jobs/... → the company, when the feed's own
    company field is missing and the title carries no separator."""
    m = re.search(r"/companies/([^/]+)/", url)
    if not m:
        return ""
    return re.sub(r"[-_]+", " ", m.group(1)).strip()


def _from_rss(url: str, source: str, limit: int = 150) -> list[RawJob]:
    feed = feedparser.parse(url, request_headers=HEADERS)
    jobs: list[RawJob] = []
    for entry in feed.entries[:limit]:
        title = str(entry.get("title", ""))
        company, role = _split_company_title(title)
        if not _is_qa(role):
            continue
        link = str(entry.get("link", ""))
        # feeds expose the company explicitly; the slug in the job URL is the
        # fallback. An empty company blinds the company blacklist + us_only list
        # and leaves the posting with no owner at all.
        company = (
            str(entry.get("himalayasjobs_companyname") or "").strip()
            or _company_from_url(link)
            or company
        ).strip()
        desc = str(entry.get("summary", "") or entry.get("content", [{}])[0].get("value", ""))
        # feeds expose the hiring region under different keys — never drop it,
        # it is the region-lock signal the location tagger relies on
        location = str(
            entry.get("himalayasjobs_locationrestriction")
            or entry.get("region")
            or entry.get("location")
            or ""
        ).strip()
        jobs.append(
            RawJob(
                source=source,
                source_url=link,
                company=company,
                title=role,
                location=location,
                description=re.sub(r"<[^>]+>", " ", desc)[:8000],
                posted_at=str(entry.get("published", "")) or None,
            )
        )
    return jobs


def collect_himalayas() -> list[RawJob]:
    return _from_rss("https://himalayas.app/jobs/rss", "himalayas")


def collect_weworkremotely() -> list[RawJob]:
    return _from_rss("https://weworkremotely.com/remote-jobs.rss", "weworkremotely")


def collect_workingnomads() -> list[RawJob]:
    return _from_rss("https://www.workingnomads.com/rss.svc", "workingnomads")


def collect_remotive() -> list[RawJob]:
    resp = httpx.get(
        "https://remotive.com/api/remote-jobs",
        params={"search": "QA", "limit": 100},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    jobs: list[RawJob] = []
    for j in resp.json().get("jobs", []):
        title = str(j.get("title", ""))
        if not _is_qa(title):
            continue
        jobs.append(
            RawJob(
                source="remotive",
                source_url=str(j.get("url", "")),
                company=str(j.get("company_name", "")),
                title=title,
                location=str(j.get("candidate_required_location", "")),
                description=re.sub(r"<[^>]+>", " ", str(j.get("description", "")))[:8000],
                salary_text=str(j.get("salary", "")),
                posted_at=str(j.get("publication_date", "")) or None,
            )
        )
    return jobs


def collect_remoteok() -> list[RawJob]:
    resp = httpx.get("https://remoteok.com/api", headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs: list[RawJob] = []
    for j in resp.json():
        if not isinstance(j, dict) or "position" not in j:
            continue  # first array element is API metadata
        title = str(j.get("position", ""))
        if not _is_qa(title):
            continue
        salary = ""
        if j.get("salary_min") or j.get("salary_max"):
            salary = f"${j.get('salary_min', '?')}-${j.get('salary_max', '?')}"
        jobs.append(
            RawJob(
                source="remoteok",
                source_url=str(j.get("url", "")),
                company=str(j.get("company", "")),
                title=title,
                location=str(j.get("location", "")) or "Worldwide",
                description=re.sub(r"<[^>]+>", " ", str(j.get("description", "")))[:8000],
                salary_text=salary,
                posted_at=str(j.get("date", "")) or None,
            )
        )
    return jobs


COLLECTOR_MAP = {
    "himalayas": collect_himalayas,
    "remotive": collect_remotive,
    "remoteok": collect_remoteok,
    "weworkremotely": collect_weworkremotely,
    "workingnomads": collect_workingnomads,
}


def collect(cfg: dict) -> list[RawJob]:
    rc = cfg.get("collectors", {}).get("remote_boards", {})
    if not rc.get("enabled"):
        return []
    jobs: list[RawJob] = []
    for name, fn in COLLECTOR_MAP.items():
        if not rc.get(name, False):
            continue
        try:
            found = fn()
            log.info("%s: %d QA jobs", name, len(found))
            jobs.extend(found)
        except Exception:
            log.warning("%s collector failed", name, exc_info=True)
    return jobs
