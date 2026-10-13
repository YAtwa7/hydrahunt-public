"""DorkLab collector — runs the active dork population as a source.

Owns its own DB connection (WAL mode allows concurrent access from the
scan worker) so it fits the uniform collector signature fn(cfg).
"""

from __future__ import annotations

import logging
import re

from app.collectors.base import RawJob
from app.config import AppConfig
from app.db import connect
from app.dorklab.engine import run_active_dorks, seed_dorks

log = logging.getLogger("hydrahunt.dorklab.collector")

QA_NET = re.compile(
    r"\b(qa|quality assurance|quality engineer|sdet|testing|test engineer|"
    r"software tester|hiring|join our team)\b",
    re.IGNORECASE,
)


def _qa_check(title: str) -> bool:
    return bool(QA_NET.search(title or ""))


def collect(cfg: AppConfig) -> list[RawJob]:
    dc = cfg.raw.get("collectors", {}).get("dorklab", {})
    if not dc.get("enabled"):
        return []
    conn = connect(cfg.db_path)
    try:
        seeded = seed_dorks(conn, dc.get("seed_file"))
        if seeded:
            log.info("seeded %d dorks", seeded)
        jobs, errors = run_active_dorks(
            conn, _qa_check, max_dorks=int(dc.get("max_dorks_per_run", 8))
        )
        for e in errors:
            log.warning(e)
        return jobs
    finally:
        conn.close()
