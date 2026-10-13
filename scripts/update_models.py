"""Refresh the OpenRouter free-model list — free/stealth models rotate often.

Run:  .venv\\Scripts\\python.exe scripts\\update_models.py
Prints current :free models so you can paste them into config.yml llm.providers.
"""

import os
import sys

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
load_dotenv()

key = os.getenv("OPENROUTER_API_KEY", "")
resp = httpx.get(
    "https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {key}"} if key else {},
    timeout=30.0,
)
resp.raise_for_status()
models = resp.json().get("data", [])

def _is_free(m: dict) -> bool:
    if m.get("id", "").endswith(":free"):
        return True
    try:
        pricing = m.get("pricing", {})
        return float(pricing.get("prompt") or 1) == 0 and float(pricing.get("completion") or 1) == 0
    except (TypeError, ValueError):
        return False


free = [m for m in models if _is_free(m)]
print(f"{len(free)} free models on OpenRouter right now:\n")
for m in sorted(free, key=lambda x: x.get("context_length") or 0, reverse=True):
    ctx = (m.get("context_length") or 0) // 1000
    print(f"  {m['id']:<55} {ctx:>5}K ctx")
print("\nPaste 3-5 IDs into config.yml -> llm.providers[0].models (openrouter section).")
