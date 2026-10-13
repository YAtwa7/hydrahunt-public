"""JobSpy collector — LinkedIn (guest), Indeed, Google Jobs, ZipRecruiter, Bayt.

No login, no account. LinkedIn runs through the public guest endpoint.
Each board is attempted independently; failures are logged, not fatal.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.collectors.base import RawJob

log = logging.getLogger("jobhunt.jobspy")

# Glassdoor is the most captcha-prone; keep available but off the default path.
SUPPORTED = {"linkedin", "indeed", "google", "zip_recruiter", "bayt", "glassdoor"}


def _scrape_board(board: str, term: str, cfg: dict[str, Any]) -> Any:
    from jobspy import scrape_jobs  # imported lazily; heavy import

    kwargs: dict[str, Any] = {
        "site_name": [board],
        "results_wanted": cfg.get("results_per_board", 50),
        "hours_old": cfg.get("hours_old", 72),
        "verbose": 0,
    }
    if board == "google":
        kwargs["google_search_term"] = f"{term} (remote OR worldwide)"
    else:
        kwargs["search_term"] = term
        kwargs["location"] = "remote"
    if board == "indeed":
        kwargs["country_indeed"] = cfg.get("country_indeed") or "usa"
    return scrape_jobs(**kwargs)


PER_PAIR_TIMEOUT_S = 240  # a hung board gets abandoned, scan moves on
MAX_WORKERS = 3

# pandas uses these for "column absent/NaN"; str() turns them into the plausible-
# looking strings "nan"/"None" once they reach the DB.
_NULLISH = {"nan", "none", "null", "nat", "n/a", ""}


def _clean(value: Any) -> str:
    """Normalize a scraped field: no NaN leakage, no whitespace padding."""
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in _NULLISH else text


def _scrape_pair(board: str, term: str, jc: dict[str, Any]) -> list[RawJob]:
    df = _scrape_board(board, term, jc)
    jobs: list[RawJob] = []
    if df is None or df.empty:
        return jobs
    for row in df.to_dict("records"):
        posted = row.get("date_posted")
        salary_text = ""
        if row.get("min_amount"):
            interval = str(row.get("interval") or "yearly")
            cur = str(row.get("currency") or "$")
            salary_text = f"{cur}{row.get('min_amount')}-{cur}{row.get('max_amount')} {interval}"
        # pandas NaN (missing column value) must never reach the pipeline as the
        # literal string "nan" — it silently defeats every region signal.
        location = _clean(row.get("location"))
        company = _clean(row.get("company"))
        jobs.append(
            RawJob(
                source=str(row.get("site") or board),
                source_url=str(row.get("job_url") or ""),
                company=company,
                title=str(row.get("title") or ""),
                location=location,
                description=str(row.get("description") or ""),
                salary_text=salary_text,
                posted_at=str(posted) if posted and str(posted) != "NaT" else None,
            )
        )
    return jobs


def collect(cfg: dict[str, Any]) -> list[RawJob]:
    jc = cfg.get("collectors", {}).get("jobspy", {})
    if not jc.get("enabled"):
        return []
    boards = [b for b in jc.get("boards", []) if b in SUPPORTED]
    terms = jc.get("search_terms", ["QA Engineer remote"])
    pairs = [(b, t) for b in boards for t in terms]

    jobs: list[RawJob] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_scrape_pair, b, t, jc): (b, t) for b, t in pairs}
        for future in as_completed(futures):
            board, term = futures[future]
            try:
                found = future.result(timeout=PER_PAIR_TIMEOUT_S)
                jobs.extend(found)
                log.info("JobSpy %s/%r: %d jobs", board, term, len(found))
            except Exception:
                log.warning("JobSpy board=%s term=%r failed/timed out", board, term, exc_info=True)
    log.info("JobSpy collected %d raw jobs across %d pairs", len(jobs), len(pairs))
    return jobs
