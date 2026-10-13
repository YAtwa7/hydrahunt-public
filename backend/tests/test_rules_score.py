"""Tests for automation classification and deterministic scoring."""

from app.collectors.base import RawJob
from app.config import load_config
from app.pipeline.rules_score import (
    classify_automation,
    detect_email_apply,
    score_job,
    tier_from_score,
)


def _job(description="", title="QA Engineer", location="Worldwide") -> RawJob:
    return RawJob(source="t", source_url="u", company="Acme", title=title,
                  location=location, description=description)


def test_automation_none():
    job = _job(description="Manual testing, test plans, exploratory sessions.")
    assert classify_automation(job) == "none"


def test_automation_plus():
    job = _job(description="Manual QA role. Automation experience with Playwright is a plus.")
    assert classify_automation(job) == "plus"


def test_automation_required():
    job = _job(description="5+ years of test automation with Selenium is required for this SDET role.")
    assert classify_automation(job) == "required"


def test_automation_open_to_learn():
    job = _job(description="Exposure to automation is expected; willing to learn Cypress is fine.")
    assert classify_automation(job) == "open_to_learn"


def test_email_apply_detection():
    job = _job(description="To apply, send your resume to jobs@acme.io with a short note.")
    assert detect_email_apply(job) is True


def test_strong_match_scores_high():
    cfg = load_config()
    desc = (
        "Manual testing, regression testing, api testing with Postman. "
        "GenAI testing of LLM features, metamorphic testing, hallucination detection. "
        "Playwright is a plus. Work from anywhere."
    )
    job = _job(description=desc)
    score = score_job(job, cfg, "ANYWHERE", "plus")
    assert score >= 70, f"strong profile match should score high, got {score}"


def test_weak_match_scores_low():
    cfg = load_config()
    job = _job(description="Onsite payroll software testing. COBOL. Must know Selenium, 5+ years required.")
    score = score_job(job, cfg, "HYBRID_ONSITE", "required")
    assert score < 55


def test_tiers():
    assert tier_from_score(80) == "A"
    assert tier_from_score(60) == "B"
    assert tier_from_score(30) == "C"
    assert tier_from_score(60, llm_score=80) == "A"
