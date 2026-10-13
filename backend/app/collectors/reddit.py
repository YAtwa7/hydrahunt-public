"""Reddit collector — public RSS feeds, NO API registration needed.

Uses reddit's built-in RSS: /r/<sub>/search.rss?q=...&restrict_sr=1
Paced requests, graceful on 403/429 (reddit throttles aggressively —
failures just mean fewer leads this scan, never a crash).
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote

import feedparser

from app.collectors.base import RawJob

log = logging.getLogger("hydrahunt.reddit")

HEADERS = {"User-Agent": "HydraHunt/0.4 (personal job search; contact: local user)"}
QUERY = 'QA OR "quality assurance" OR "test engineer" OR "software tester"'
TITLE_HINT = re.compile(r"hiring|job|position|role|opening|qa|test", re.IGNORECASE)
REQUEST_GAP_S = 3.0


HOSTS = ("https://www.reddit.com", "https://old.reddit.com")


def _search_rss(sub: str) -> list[RawJob]:
    """Try both hosts; 429/403 means IP-throttled — skip, succeed another day."""
    feed = None
    for host in HOSTS:
        url = f"{host}/r/{sub}/search.rss?q={quote(QUERY)}&restrict_sr=1&sort=new&t=month"
        candidate = feedparser.parse(url, request_headers=HEADERS)
        if candidate.entries:
            feed = candidate
            break
    if feed is None:
        raise RuntimeError(f"r/{sub}: throttled on all hosts")
    jobs = []
    for entry in feed.entries[:25]:
        title = str(entry.get("title", ""))
        if not TITLE_HINT.search(title):
            continue
        jobs.append(
            RawJob(
                source=f"reddit:r/{sub}",
                source_url=str(entry.get("link", "")),
                company="",
                title=title[:200],
                description=re.sub(r"<[^>]+>", " ", str(entry.get("summary", "")))[:8000],
                job_type="lead",
                posted_at=str(entry.get("published", "")) or None,
                meta={"author": str(entry.get("author", ""))},
            )
        )
    return jobs


def collect(cfg: dict, settings=None) -> list[RawJob]:
    rc = cfg.get("collectors", {}).get("reddit", {})
    if not rc.get("enabled"):
        return []
    jobs: list[RawJob] = []
    for sub in rc.get("subreddits", []):
        try:
            found = _search_rss(sub)
            jobs.extend(found)
            log.info("r/%s: %d leads", sub, len(found))
        except Exception as exc:
            # normal on hot IPs; next scan (or tomorrow) usually works
            log.info("r/%s skipped: %s", sub, exc)
        time.sleep(REQUEST_GAP_S)
    log.info("Reddit: %d leads total", len(jobs))
    return jobs
