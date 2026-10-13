"""Tests for hard filters."""

from app.collectors.base import RawJob
from app.config import load_config
from app.pipeline.filters import HardFilter


def _job(**kw) -> RawJob:
    base = {"source": "t", "source_url": "http://x", "company": "Acme", "title": "QA Engineer"}
    base.update(kw)
    return RawJob(**base)


def test_target_title_passes():
    hf = HardFilter(load_config())
    assert hf.reject_reason(_job()) is None
    assert hf.reject_reason(_job(title="Manual QA Engineer")) is None


def test_unrelated_title_rejected():
    hf = HardFilter(load_config())
    assert hf.reject_reason(_job(title="Senior Frontend Developer")) == "title not targeted"


def test_excluded_region_in_location():
    hf = HardFilter(load_config())
    reason = hf.reject_reason(_job(location="Bangalore, India"))
    assert reason == "excluded region: india"


def test_excluded_region_in_description():
    hf = HardFilter(load_config())
    job = _job(location="Remote", description="Candidates must be based in Pakistan only.")
    assert hf.reject_reason(job) == "excluded region: pakistan"


def test_blocklisted_title():
    hf = HardFilter(load_config())
    assert hf.reject_reason(_job(title="QA Manager")) == "title blocklist"


def test_lead_uses_qa_safety_net():
    hf = HardFilter(load_config())
    lead = _job(job_type="lead", title="We're hiring! Testing role at startup")
    assert hf.reject_reason(lead) is None
    bad_lead = _job(job_type="lead", title="Looking for a designer")
    assert hf.reject_reason(bad_lead) == "lead not QA-related"
