"""DorkLab evolution — daily LLM-driven dork breeding.

Cadence comes from config: scheduler.dorklab_evolution_time, default 10:00.

GENERATE → EXECUTE → SCORE → EVOLVE:
- Top dorks get mutated/crossed by the LLM into a new generation
- Bottom dorks (fitness < threshold after enough runs) get retired
- Children inherit family; parent lineage stored
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from app.config import AppConfig
from app.scoring.llm import _call_provider, _provider_keys

log = logging.getLogger("hydrahunt.dorklab.evolve")

RETIRE_MIN_RUNS = 3
RETIRE_MAX_FITNESS = 0.05
BREED_TOP_N = 5

EVOLVE_PROMPT = """You are a Google-dork breeding engine for a QA job-hunting system.
The candidate: manual/software QA engineer in Egypt, remote-anywhere or relocation.
Here are our top-performing dorks with fitness (0-1):

{dorks}

Breed {n} NEW dorks by mutating and crossing these winners:
- vary site: targets (greenhouse/lever/ashby/workable/notion.site/linkedin.com/posts/x.com)
- vary phrasings ("we are hiring", "join our team", "send your resume", "apply by email")
- vary QA terms (QA engineer, software tester, quality engineer, manual QA, test analyst)
- keep them precise: junk results kill fitness
Families: ats | email_apply | linkedin_posts | x_posts | notion | funding | niche
Return STRICT JSON: {{"dorks": [{{"query": "...", "family": "ats"}}]}}"""


def _next_generation(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(generation), 0) g FROM dorks").fetchone()
    return row["g"] + 1


def retire_losers(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE dorks SET retired=1 WHERE runs>=? AND fitness<? AND retired=0",
        (RETIRE_MIN_RUNS, RETIRE_MAX_FITNESS),
    )
    conn.commit()
    if cur.rowcount:
        log.info("retired %d underperforming dorks", cur.rowcount)
    return cur.rowcount


def breed(conn: sqlite3.Connection, cfg: AppConfig, n_children: int = 5) -> int:
    """LLM-generate children from top dorks. Returns count created."""
    top = conn.execute(
        "SELECT id, query, family, fitness FROM dorks WHERE retired=0 "
        "ORDER BY fitness DESC, valid_results DESC LIMIT ?",
        (BREED_TOP_N,),
    ).fetchall()
    if not top:
        return 0

    dorks_text = "\n".join(f'- [{d["family"]}] (fitness {d["fitness"]:.2f}) {d["query"]}' for d in top)
    prompt = EVOLVE_PROMPT.format(dorks=dorks_text, n=n_children)

    keys = _provider_keys(cfg)
    result = None
    for provider in cfg.raw.get("llm", {}).get("providers", []):
        api_key = keys.get(provider.get("name", ""), "")
        if not api_key:
            continue
        for model in provider.get("models", []):
            result = _call_provider(
                provider, api_key, model, prompt,
                system="You are a Google-dork breeding engine. Return STRICT JSON only.",
            )
            if result:
                break
        if result:
            break
    if not result or not isinstance(result.get("dorks"), list):
        log.warning("evolution: no valid LLM response")
        return 0

    generation = _next_generation(conn)
    parent_ids = json.dumps([d["id"] for d in top])
    created = 0
    for child in result["dorks"][:n_children]:
        query = str(child.get("query", "")).strip()
        if not query or len(query) < 15:
            continue
        dork_id = f"gen{generation}-{uuid.uuid4().hex[:6]}"
        conn.execute(
            """INSERT INTO dorks (id, query, family, fitness, generation, parent_ids)
               VALUES (?,?,?,0.5,?,?)""",  # children start at optimistic 0.5 prior
            (dork_id, query, child.get("family", "misc"), generation, parent_ids),
        )
        created += 1
    conn.commit()
    log.info("evolution: bred %d generation-%d dorks", created, generation)
    return created


def evolve(conn: sqlite3.Connection, cfg: AppConfig) -> dict:
    retired = retire_losers(conn)
    bred = breed(conn, cfg)
    return {"retired": retired, "bred": bred}
