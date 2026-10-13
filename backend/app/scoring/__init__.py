"""LLM scoring — the only stage that spends tokens.

Contract: `score_with_llm` never raises. It walks the configured provider
chain and returns None if nothing answers, so a scan continues on rule
scores alone. HTTP >= 400, empty content and malformed JSON are all
indistinguishable on purpose — the chain just moves to the next model.

Two consequences worth knowing:

- the first working model is memoised (`_last_good`), because free models
  rot mid-week and re-punching a dead one costs a 60s timeout per job;
- the LLM score *replaces* the rule score when present. It is not blended.
"""
