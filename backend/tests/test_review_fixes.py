"""Regression tests for PR #2 review findings."""

from app.collectors.base import RawJob
from app.config import load_config
from app.pipeline.filters import HardFilter
from app.pipeline.location import detect_region_lock, tag_location
from app.pipeline.salary import estimate

EXCLUDED = ["india", "pakistan", "bangladesh"]


def _job(location="", description="", salary_text="") -> RawJob:
    return RawJob(source="t", source_url="u", company="c", title="QA Engineer",
                  location=location, description=description, salary_text=salary_text)


def test_us_only_beats_anywhere():
    job = _job(description="Work from anywhere in the US. Must be authorized to work in the U.S.")
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_us_only_variants():
    assert tag_location(_job(description="US only applicants."), EXCLUDED) == "EXCLUDED"
    assert tag_location(_job(location="Remote (United States only)"), EXCLUDED) == "EXCLUDED"


def test_indianapolis_not_india():
    # "india" must not word-match "Indianapolis"; the city is US-located, so it
    # is now region-locked as a US role (correct), but never as "India".
    job = _job(location="Indianapolis, IN")
    assert detect_region_lock(job) != "india"
    assert detect_region_lock(job) == "united states"
    hf = HardFilter(load_config())
    assert hf.reject_reason(_job(location="Indianapolis, IN")) is None


def test_india_still_excluded():
    assert tag_location(_job(location="Pune, India"), EXCLUDED) == "EXCLUDED"


def test_monthly_salary_not_divided():
    job = _job(salary_text="$4,000-$6,000 monthly")
    est = estimate(job, "ANYWHERE", {})
    assert est["min"] == 4000 and est["max"] == 6000


def test_yearly_salary_divided():
    job = _job(salary_text="$48,000-$72,000 per year")
    est = estimate(job, "ANYWHERE", {})
    assert est["min"] == 4000 and est["max"] == 6000
