"""Tests for salary estimation."""

from app.collectors.base import RawJob
from app.pipeline.salary import estimate


def _job(salary_text="", description="") -> RawJob:
    return RawJob(source="t", source_url="u", company="c", title="Senior QA Engineer",
                  description=description, salary_text=salary_text)


def test_posted_salary_anchor():
    job = _job(description="Salary range: $60,000 - $84,000 per year.")
    est = estimate(job, "ANYWHERE", {})
    assert est["kind"] == "posted"
    assert est["confidence"] == "high"
    assert 4500 <= est["min"] <= 5500
    assert 6500 <= est["max"] <= 7500


def test_benchmark_senior():
    est = estimate(_job(), "ANYWHERE", {})
    assert est["min"] >= 3000  # senior benchmark floor
    assert est["confidence"] == "medium"


def test_display_has_usd_and_egp():
    est = estimate(_job(), "UNKNOWN", {})
    assert "$" in est["display"] and "E£" in est["display"]
