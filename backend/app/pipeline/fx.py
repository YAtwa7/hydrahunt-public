"""Currency conversion — live rates with static fallback.

Live source: open.er-api.com (free, no key, daily). Cached 24h in
data/fx_rates.json. If fetch fails, static defaults are used.
All rates are units-per-1-USD.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("hydrahunt.fx")

CACHE = Path(__file__).resolve().parents[3] / "data" / "fx_rates.json"
LIVE_URL = "https://open.er-api.com/v6/latest/USD"
TTL_SECONDS = 24 * 3600

STATIC_RATES = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.79,
    "EGP": 48.5,
    "AED": 3.67,
    "SAR": 3.75,
    "CAD": 1.36,
    "AUD": 1.52,
    "CHF": 0.88,
}

SYMBOL_TO_CODE = {"$": "USD", "€": "EUR", "£": "GBP", "E£": "EGP", "ج.م": "EGP"}


def get_rates() -> dict[str, float]:
    """Live rates if fresh cache or fetch succeeds; static otherwise."""
    if CACHE.exists():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - data.get("fetched_at", 0) < TTL_SECONDS:
                return data["rates"]
        except (json.JSONDecodeError, KeyError):
            pass
    try:
        resp = httpx.get(LIVE_URL, timeout=10.0)
        resp.raise_for_status()
        rates = {k: float(v) for k, v in resp.json().get("rates", {}).items()}
        merged = {**STATIC_RATES, **rates}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"fetched_at": time.time(), "rates": merged}))
        return merged
    except Exception:
        log.warning("live FX fetch failed, using static rates", exc_info=True)
        return STATIC_RATES


def to_usd(amount: float, currency: str, rates: dict[str, float] | None = None) -> float:
    rates = rates or get_rates()
    rate = rates.get(currency.upper())
    if not rate:
        log.warning("unknown currency %r — treating as USD", currency)
        return amount
    return amount / rate


def convert(amount: float, from_cur: str, to_cur: str, rates: dict[str, float] | None = None) -> float:
    rates = rates or get_rates()
    usd = to_usd(amount, from_cur, rates)
    return usd * rates.get(to_cur.upper(), 1.0)
