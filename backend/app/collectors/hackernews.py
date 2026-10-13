"""Hacker News 'Who is Hiring?' collector — free Algolia API, no auth.

Finds the latest monthly thread by 'whoishiring' and parses top-level
comments as job leads. Founders post here before boards — highest edge.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

from app.collectors.base import RawJob

log = logging.getLogger("jobhunt.hn")

TIMEOUT = httpx.Timeout(20.0)
QA_PATTERN = re.compile(r"\b(qa|quality assurance|sdet|test engineer|testing)\b", re.IGNORECASE)


def _latest_thread() -> int | None:
    resp = httpx.get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"tags": "story", "query": '"Who is hiring?"', "hitsPerPage": 10},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    for hit in resp.json().get("hits", []):
        if hit.get("author") == "whoishiring" and "Who is hiring" in hit.get("title", ""):
            return int(hit["objectID"])
    return None


def _first_line(text: str) -> str:
    line = (text or "").strip().split("\n", 1)[0]
    return re.sub(r"<[^>]+>", "", line)[:120]


def collect(cfg: dict) -> list[RawJob]:
    if not cfg.get("collectors", {}).get("hackernews", {}).get("enabled"):
        return []
    try:
        thread_id = _latest_thread()
        if not thread_id:
            return []
        resp = httpx.get(f"https://hn.algolia.com/api/v1/items/{thread_id}", timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception:
        log.warning("HN collector failed", exc_info=True)
        return []

    jobs: list[RawJob] = []
    for child in resp.json().get("children", []):
        text = str(child.get("text") or "")
        if not text or not QA_PATTERN.search(text):
            continue
        head = _first_line(text)
        company = head.split("|")[0].split("—")[0].strip()[:60]
        plain = re.sub(r"<[^>]+>", " ", text)
        jobs.append(
            RawJob(
                source="hackernews",
                source_url=f"https://news.ycombinator.com/item?id={child.get('id')}",
                company=company or "HN Who is Hiring",
                title=head or "QA role (HN Who is Hiring)",
                description=plain[:8000],
                job_type="lead",
                posted_at=datetime.fromtimestamp(
                    int(child.get("created_at_i", 0)), tz=UTC
                ).isoformat(),
                meta={"author": child.get("author", "")},
            )
        )
    log.info("HN Who is Hiring: %d QA leads", len(jobs))
    return jobs
