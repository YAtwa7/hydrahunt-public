"""Zero-token pipeline: dedup, hard filters, location tags, rules scoring.

Everything in here is deterministic regex/table work — no network, no LLM.
The LLM stage only ever sees the top ~20 jobs that this package ranked
first, so the token cost of a scan is bounded no matter how many jobs the
collectors found.

Invariants:

- `HardFilter.reject_reason` is ordered and first-match-wins. Rejected jobs
  are dropped entirely and never written to the database.
- `tag_location` walks an ordered cascade; the ordering is load-bearing
  (e.g. US-only is checked before "work from anywhere", which is the
  difference between reachable and not).
"""
