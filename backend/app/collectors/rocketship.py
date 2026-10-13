"""Remote Rocketship collector — career-page aggregator via public sitemaps.

Covers the long tail LinkedIn misses: small companies posting only on their
own career pages (breezy/recruitee/custom). Sitemaps give us opening URLs;
each job page is parsed for company/title/apply-link/description.
Free, no auth. Paced politely.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from app.collectors.base import RawJob

log = logging.getLogger("hydrahunt.rocketship")

SITEMAPS = [
    "https://www.remoterocketship.com/sitemap_job_openings_worldwide.xml",
    "https://www.remoterocketship.com/sitemap_job_openings_rest_of_world.xml",
    "https://www.remoterocketship.com/sitemap_job_openings_rest_of_world_2.xml",
]
HEADERS = {"User-Agent": "HydraHunt/0.4 (personal job search tool)"}
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
JOB_URL_RE = re.compile(r"/company/([^/]+)/jobs/([^/]+)/?$")
QA_SLUG_RE = re.compile(
    r"\b(qa|quality|tester|testing|test-engineer|sdet|test-analyst|qc)\b", re.IGNORECASE
)
POSTED_RE = re.compile(r"(\d+\s+(?:hours?|days?|weeks?|months?)\s+ago|today|yesterday)", re.I)
SLUG_LOCATION_RE = re.compile(
    r"\b(worldwide|global|emea|europe|europa|usa|united-states|united-kingdom|uk|"
    r"canada|australia|new-zealand|brazil|brasil|mexico|colombia|chile|peru|argentina|"
    r"uruguay|latam|latin-america|india|germany|deutschland|france|spain|espana|"
    r"netherlands|belgium|switzerland|austria|ireland|poland|polska|portugal|italy|"
    r"sweden|norway|denmark|finland|philippines|uae|dubai|saudi|egypt|south-africa|"
    r"nigeria|kenya|singapore|malaysia|indonesia|vietnam|thailand|japan|south-korea|"
    r"turkey|romania|czech|hungary|ukraine)\b",
    re.IGNORECASE,
)
_SLUG_COUNTRY_MAP = {
    "usa": "United States", "united-states": "United States",
    "uk": "United Kingdom", "united-kingdom": "United Kingdom",
    "uae": "UAE", "dubai": "UAE", "saudi": "Saudi Arabia",
    "brasil": "Brazil", "deutschland": "Germany", "espana": "Spain",
    "latam": "Latin America", "latin-america": "Latin America",
    "polska": "Poland",
}


def _slug_location(slug: str) -> str:
    """Honest location from the RR slug. Unknown = 'Remote' (no worldwide claim:
    a career-page job with no country marker must not get the ANYWHERE boost)."""
    s = slug.lower()
    m = SLUG_LOCATION_RE.search(s)
    if not m:
        return "Remote"
    token = m.group(0).lower()
    if token in ("worldwide", "global"):
        return "Worldwide (remote)"
    if token == "emea":
        return "EMEA"
    return _SLUG_COUNTRY_MAP.get(token, token.replace("-", " ").title())
SECTION_RE = {
    "description": re.compile(r"Description(.+?)(?:Requirements|Benefits|Similar Jobs)", re.S),
    "requirements": re.compile(r"Requirements(.+?)(?:Benefits|Similar Jobs)", re.S),
}
APPLY_HOST_RE = re.compile(
    r"https?://[^\"'\s]*(?:careers|jobs|apply|breezy\.hr|recruitee|lever\.co|"
    r"greenhouse\.io|workable|ashbyhq|smartrecruiters|zohorecruit)[^\"'\s]*",
    re.IGNORECASE,
)
AGE_MAP = {"today": 0, "yesterday": 1}
MAX_PAGES_PER_SCAN = 40
REQUEST_GAP_S = 1.5


def _qa_opening_urls(client: httpx.Client) -> list[str]:
    urls: list[str] = []
    for sitemap in SITEMAPS:
        try:
            resp = client.get(sitemap, timeout=30)
            resp.raise_for_status()
        except httpx.HTTPError:
            log.warning("sitemap failed: %s", sitemap, exc_info=True)
            continue
        for loc in LOC_RE.findall(resp.text):
            slug = loc.rstrip("/").rsplit("/", 1)[-1]
            if QA_SLUG_RE.search(slug.replace("-", " ")):
                urls.append(loc)
    return list(dict.fromkeys(urls))  # dedupe, keep order


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _parse_page(client: httpx.Client, url: str) -> RawJob | None:
    m = JOB_URL_RE.search(url)
    if not m:
        return None
    company = m.group(1).replace("-", " ").title()
    slug = m.group(2)
    title = re.sub(r"-(worldwide|remote|us|uk|emea).*$", "", slug).replace("-", " ").title()

    resp = client.get(url, timeout=30)
    if resp.status_code != 200:
        return None
    html = resp.text

    # prefer links that look like an actual job posting (path with jobs/apply/p/o),
    # not the company's root careers page
    candidates = [
        link.rstrip("/") for link in APPLY_HOST_RE.findall(html) if "remoterocketship" not in link
    ]
    apply_url = ""
    for link in candidates:
        if re.search(r"/(jobs?|p|o|postings|vacancies|apply)/", link, re.IGNORECASE):
            apply_url = link
            break
    if not apply_url and candidates:
        apply_url = candidates[0]

    desc_m = SECTION_RE["description"].search(html)
    req_m = SECTION_RE["requirements"].search(html)
    description = " ".join(
        part for part in (_strip(desc_m.group(1)) if desc_m else "",
                          _strip(req_m.group(1)) if req_m else "") if part
    )[:8000]

    posted_m = POSTED_RE.search(html)
    posted_hint = posted_m.group(1) if posted_m else ""

    return RawJob(
        source="rocketship",
        source_url=apply_url or url,  # prefer the direct apply link
        company=company,
        title=title,
        location=_slug_location(slug),
        description=description,
        posted_at=None,
        meta={"rr_url": url, "posted_hint": posted_hint},
    )


def _feed_company_discovery(client: httpx.Client, db_path) -> int:
    """Open-ended company discovery: crawl RR's company sitemap and feed
    every company into the watchlist — snowball promotes the QA-relevant
    ones over time. Idempotent, cheap (one sitemap fetch)."""
    from app.db import connect
    from app.pipeline.companies import discover

    try:
        resp = client.get(
            "https://www.remoterocketship.com/sitemap_companies.xml", timeout=30
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        log.warning("company sitemap failed", exc_info=True)
        return 0
    slugs = set()
    for loc in LOC_RE.findall(resp.text):
        m = re.search(r"/company/([^/]+)/?$", loc)
        if m:
            slugs.add(m.group(1))
    conn = connect(db_path)
    try:
        for slug in slugs:
            discover(conn, slug.replace("-", " ").title())
        conn.commit()
    finally:
        conn.close()
    log.info("rocketship: %d companies fed to watchlist", len(slugs))
    return len(slugs)


def collect(cfg) -> list[RawJob]:
    raw = cfg.raw if hasattr(cfg, "raw") else cfg
    if not raw.get("collectors", {}).get("remote_boards", {}).get("enabled"):
        return []
    jobs: list[RawJob] = []
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        if hasattr(cfg, "db_path"):
            _feed_company_discovery(client, cfg.db_path)
        try:
            urls = _qa_opening_urls(client)
        except Exception:
            log.warning("rocketship sitemap crawl failed", exc_info=True)
            return []
        log.info("rocketship: %d QA openings across sitemaps", len(urls))
        for url in urls[:MAX_PAGES_PER_SCAN]:
            try:
                job = _parse_page(client, url)
                if job:
                    jobs.append(job)
            except Exception:
                log.warning("rocketship page failed: %s", url, exc_info=True)
            time.sleep(REQUEST_GAP_S)
    log.info("rocketship: %d jobs parsed", len(jobs))
    return jobs
