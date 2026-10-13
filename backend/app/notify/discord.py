"""Discord webhook notifier — rich embeds for new jobs.

A-tier → instant messages. Digest → one summary message.
Webhook URL comes from .env (DISCORD_WEBHOOK_URL); never logged.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

import httpx

log = logging.getLogger("jobhunt.discord")

TIER_COLORS = {"A": 0x2ECC71, "B": 0xF39C12, "C": 0x95A5A6}
TIER_EMOJI = {"A": "🟢", "B": "🟡", "C": "⚪"}
MAX_EMBEDS = 10  # Discord limit per message


def _company_logo(company: str) -> str:
    slug = "".join(c for c in company.lower() if c.isalnum())
    return f"https://logo.clearbit.com/{slug}.com" if slug else ""


def _embed(job: sqlite3.Row) -> dict:
    tier = job["tier"]
    fields = [
        {"name": "Score", "value": str(job["llm_score"] or job["rule_score"]), "inline": True},
        {"name": "Automation", "value": job["automation_class"], "inline": True},
        {"name": "Location", "value": job["location"] or job["location_tag"], "inline": True},
    ]
    if job["salary_estimate"]:
        fields.append({"name": "Est. salary", "value": job["salary_estimate"], "inline": True})
    if job["posted_at"]:
        fields.append({"name": "Posted", "value": str(job["posted_at"])[:10], "inline": True})
    return {
        "title": f"{TIER_EMOJI.get(tier, '')} {job['title']}",
        "url": job["source_url"],
        "description": f"**{job['company']}** · via {job['source']}",
        "color": TIER_COLORS.get(tier, TIER_COLORS["C"]),
        "thumbnail": {"url": _company_logo(job["company"])},
        "fields": fields,
        "footer": {"text": f"Found {job['first_seen_at'][:16]} · id {job['id']}"},
    }


def notify_new_jobs(webhook_url: str, jobs: list[sqlite3.Row]) -> int:
    """Send instant alerts for A-tier jobs. Returns count sent."""
    if not webhook_url:
        log.info("Discord webhook not configured — skipping notifications")
        return 0
    sent = 0
    batch: list[dict] = []
    for job in jobs:
        batch.append(_embed(job))
        if len(batch) == MAX_EMBEDS:
            _post(webhook_url, batch)
            sent += len(batch)
            batch = []
    if batch:
        _post(webhook_url, batch)
        sent += len(batch)
    return sent


def send_digest(webhook_url: str, stats: dict) -> None:
    """Daily digest: scan summary + tier counts."""
    if not webhook_url:
        return
    embed = {
        "title": "📋 HydraHunt — Daily Digest",
        "color": 0x3498DB,
        "fields": [
            {"name": "New jobs", "value": str(stats.get("jobs_new", 0)), "inline": True},
            {"name": "A-tier", "value": str(stats.get("tier_a", 0)), "inline": True},
            {"name": "B-tier", "value": str(stats.get("tier_b", 0)), "inline": True},
            {"name": "Scanned", "value": str(stats.get("jobs_found", 0)), "inline": True},
        ],
        "footer": {"text": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")},
    }
    _post(webhook_url, [embed])


def _post(webhook_url: str, embeds: list[dict]) -> None:
    try:
        resp = httpx.post(webhook_url, json={"embeds": embeds}, timeout=15.0)
        if resp.status_code >= 400:
            log.warning("Discord webhook returned %s", resp.status_code)
    except Exception:
        log.warning("Discord webhook failed", exc_info=True)
