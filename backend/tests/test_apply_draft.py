"""Tests for the auto-apply draft assistant (LLM mocked — no real calls)."""

import json
from unittest.mock import patch

from app.apply.drafter import _profile_text, generate_draft, get_cached, save_draft
from app.config import load_config
from app.db import connect, init_db

VALID_DRAFT = {
    "summary": "QA engineer with GenAI testing niche.",
    "why_this_role": "Manual-first role at a product company.",
    "why_you": "Direct manual QA + API testing evidence.",
    "experience_answers": [{"question": "Tell us about yourself", "answer": "..."}],
    "cover_email": "Subject: QA application\n\nHello...",
    "gaps": ["No fintech domain experience"],
}

JOB = {
    "id": 1,
    "title": "Senior Manual QA Engineer",
    "company": "Example Corp",
    "location": "Worldwide",
    "description": "Manual testing, Postman, SQL, Charles proxy...",
    "salary_text": "",
    "salary_estimate": "$4,000-6,000",
}


_FAKE_KEYS = {"openrouter": "test-key", "agnes": "test-key"}


def _keys_patch():
    """The chain skips providers with no API key, so the mock must also fake the
    key lookup — otherwise these tests only pass on a machine with a real .env."""
    return patch("app.apply.drafter._provider_keys", return_value=_FAKE_KEYS)


def test_valid_llm_response_parses():
    cfg = load_config()
    with _keys_patch(), patch("app.apply.drafter._call_provider", return_value=dict(VALID_DRAFT)):
        draft = generate_draft(JOB, cfg)
    assert draft is not None
    assert draft["summary"] == VALID_DRAFT["summary"]
    assert "gaps" in draft


def test_llm_failure_returns_none():
    cfg = load_config()
    with _keys_patch(), patch("app.apply.drafter._call_provider", return_value=None):
        assert generate_draft(JOB, cfg) is None


def test_malformed_response_returns_none():
    cfg = load_config()
    with _keys_patch(), patch("app.apply.drafter._call_provider", return_value={"wrong": "shape"}):
        assert generate_draft(JOB, cfg) is None


def test_prompt_is_evidence_bounded():
    cfg = load_config()
    captured = {}

    def spy(provider, api_key, model, prompt, system=None):
        captured["prompt"] = prompt
        captured["system"] = system
        return dict(VALID_DRAFT)

    with _keys_patch(), patch("app.apply.drafter._call_provider", side_effect=spy):
        generate_draft(JOB, cfg)

    assert captured["prompt"]
    assert captured["system"]
    assert "NEVER invent employers" in captured["system"]
    assert "CANDIDATE PROFILE (the ONLY source of truth)" in captured["prompt"]
    assert "PRE-WRITTEN CANONICAL ANSWERS" in captured["prompt"]
    assert "core skills and competencies" in captured["prompt"].lower()
    assert "metamorphic testing" in captured["prompt"]  # real CV evidence present
    assert JOB["description"][:100] in captured["prompt"]


def test_prompt_carries_canonical_answers():
    cfg = load_config()
    text = _profile_text(cfg)
    assert "PRE-WRITTEN CANONICAL ANSWERS" in text
    assert "core skills and competencies" in text.lower()
    assert "2,500" in text  # salary answer reuses the profile number
    assert "metamorphic testing" in text  # achievements block still present


def test_profile_text_contains_skills():
    cfg = load_config()
    text = _profile_text(cfg)
    assert "metamorphic testing" in text
    assert "manual testing" in text


def test_draft_cache_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    conn = connect(db)
    try:
        conn.execute(
            "INSERT INTO jobs (dedup_hash, source, source_url, company, title, first_seen_at) "
            "VALUES ('h', 's', 'u', 'c', 't', 'now')"
        )
        save_draft(conn, 1, dict(VALID_DRAFT))
        cached = get_cached(conn, 1)
        assert cached["summary"] == VALID_DRAFT["summary"]
        # overwrite (regenerate)
        updated = dict(VALID_DRAFT, summary="new summary")
        save_draft(conn, 1, updated)
        assert get_cached(conn, 1)["summary"] == "new summary"
    finally:
        conn.close()


def test_schema_json_serializable():
    json.dumps(VALID_DRAFT)  # must not raise
