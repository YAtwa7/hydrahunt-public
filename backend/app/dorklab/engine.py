"""DorkLab engine — executes dorks and tracks fitness.

Execution backend: DuckDuckGo HTML (free, no key, scrape-friendly).
Google dork syntax (site:, quotes, OR) works on DDG.
Paced requests; a CAPTCHA/block marks the run failed, not the dork.

fitness = valid_results / max(total_results, 1)  — updated after each run.
"""

from __future__ import annotations

import logging
import random
import re
import sqlite3
import time
from urllib.parse import unquote, urlparse

import httpx
import yaml

from app.collectors.base import RawJob
from app.config import ROOT

log = logging.getLogger("hydrahunt.dorklab")

DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
]
RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)
BLOCK_HINTS = ("anomaly-modal", "captcha", "are you a robot")
REQUEST_GAP_S = (7.0, 13.0)  # randomized gap between dork executions
BACKOFF_S = 60.0  # cooldown after a 403 before one retry


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def _unwrap_ddg(url: str) -> str:
    """DDG wraps links in /l/?uddg=<encoded>."""
    if "uddg=" in url:
        return unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
    return url


def _ddg_request(query: str, ua: str) -> httpx.Response:
    return httpx.post(
        DDG_URL,
        data={"q": query},
        headers={"User-Agent": ua},
        timeout=30.0,
        follow_redirects=True,
    )


def execute_dork(query: str, max_results: int = 20) -> list[dict]:
    """Run one dork with UA rotation + one backoff retry. Raises on persistent block."""
    ua = random.choice(USER_AGENTS)
    resp = _ddg_request(query, ua)
    if resp.status_code == 403:
        log.info("DDG 403 — cooling down %.0fs before one retry", BACKOFF_S)
        time.sleep(BACKOFF_S)
        ua = random.choice(USER_AGENTS)
        resp = _ddg_request(query, ua)
    resp.raise_for_status()
    if any(h in resp.text for h in BLOCK_HINTS):
        raise RuntimeError("search backend blocked the request (captcha)")
    snippets = [_strip(s) for s in SNIPPET_RE.findall(resp.text)]
    results = []
    matches = RESULT_RE.findall(resp.text)[:max_results]
    for i, (url, title) in enumerate(matches):
        results.append(
            {
                "url": _unwrap_ddg(url),
                "title": _strip(title),
                "snippet": snippets[i] if i < len(snippets) else "",
            }
        )
    return results


def results_to_jobs(results: list[dict], family: str, dork_id: str) -> list[RawJob]:
    """Convert dork hits to RawJob leads; company guessed from domain."""
    jobs = []
    for r in results:
        domain = urlparse(r["url"]).netloc.replace("www.", "")
        company = domain.split(".")[0].title() if domain else ""
        jobs.append(
            RawJob(
                source=f"dork:{family}",
                source_url=r["url"],
                company=company,
                title=r["title"][:200],
                description=f"{r['snippet']}\n\n(discovered via dork {dork_id})",
                job_type="lead",
                meta={"dork_id": dork_id, "domain": domain},
            )
        )
    return jobs


def seed_dorks(conn: sqlite3.Connection, seed_path: str | None = None) -> int:
    """Load generation-0 dorks from YAML into the DB (idempotent)."""
    path = ROOT / (seed_path or "dorks/seed_dorks.yml")
    if not path.exists():
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    count = 0
    for d in data.get("dorks", []):
        cur = conn.execute(
            """INSERT OR IGNORE INTO dorks (id, query, family, fitness, generation)
               VALUES (?,?,?,?,?)""",
            (d["id"], d["query"], d.get("family", "misc"), d.get("fitness", 0.0),
             d.get("generation", 0)),
        )
        count += cur.rowcount
    conn.commit()
    return count


def record_run(conn: sqlite3.Connection, dork_id: str, total: int, valid: int) -> None:
    """Update fitness as EMA (recent runs weigh more). A child's 0.5 prior blends in."""
    row = conn.execute("SELECT fitness FROM dorks WHERE id=?", (dork_id,)).fetchone()
    if not row:
        return
    alpha = 0.4  # EMA weight of the latest run
    run_fitness = valid / total if total > 0 else 0.0
    new_fitness = (1 - alpha) * row["fitness"] + alpha * run_fitness
    conn.execute(
        """UPDATE dorks SET fitness=?, runs=runs+1,
           total_results=total_results+?, valid_results=valid_results+? WHERE id=?""",
        (round(new_fitness, 4), total, valid, dork_id),
    )
    conn.commit()


def run_active_dorks(
    conn: sqlite3.Connection, qa_check, max_dorks: int = 8
) -> tuple[list[RawJob], list[str]]:
    """Execute top non-retired dorks. Returns (jobs, errors). qa_check(title)->bool."""
    dorks = conn.execute(
        "SELECT id, query, family FROM dorks WHERE retired=0 ORDER BY fitness DESC, runs ASC LIMIT ?",
        (max_dorks,),
    ).fetchall()
    jobs: list[RawJob] = []
    errors: list[str] = []
    for i, d in enumerate(dorks):
        try:
            results = execute_dork(d["query"])
            valid = [r for r in results if qa_check(r["title"])]
            record_run(conn, d["id"], len(results), len(valid))
            jobs.extend(results_to_jobs(valid, d["family"], d["id"]))
            log.info("dork %s: %d results, %d valid", d["id"], len(results), len(valid))
        except Exception as exc:
            # infra blocks/failures mark the RUN failed — never the dork's fitness
            log.warning("dork %s run failed (fitness untouched): %s", d["id"], exc)
            errors.append(f"dork {d['id']}: {exc}")
        if i < len(dorks) - 1:
            time.sleep(random.uniform(*REQUEST_GAP_S))
    return jobs, errors
