"""Tests for years-of-experience ceiling and supplier-QA blocklist."""

from app.collectors.base import RawJob
from app.config import load_config
from app.pipeline.filters import HardFilter


def _job(title="QA Engineer", description="") -> RawJob:
    return RawJob(source="t", source_url="u", company="Acme", title=title, description=description)


def test_15_years_rejected():
    hf = HardFilter(load_config())
    job = _job(description="QA/QE Market Lead. 15+ years of experience in quality engineering.")
    assert "15y" in hf.reject_reason(job)


def test_10_years_rejected():
    hf = HardFilter(load_config())
    job = _job(description="Minimum 10 years experience required.")
    assert "10y" in hf.reject_reason(job)


def test_5_years_passes():
    hf = HardFilter(load_config())
    job = _job(description="5+ years of experience in software testing.")
    assert hf.reject_reason(job) is None


def test_company_age_not_experience():
    hf = HardFilter(load_config())
    job = _job(description="For 15 years we have built great products. Join our QA team.")
    assert hf.reject_reason(job) is None


def test_supplier_quality_blocked():
    hf = HardFilter(load_config())
    assert hf.reject_reason(_job(title="Supplier Quality Engineer")) == "title blocklist"
    assert hf.reject_reason(_job(title="SQE – Supplier Quality")) == "title blocklist"
    assert hf.reject_reason(_job(title="Vendor Quality Analyst")) == "title blocklist"


def test_range_requirement():
    hf = HardFilter(load_config())
    job = _job(description="12-15 years of experience in QA leadership.")
    assert hf.reject_reason(job) is not None
