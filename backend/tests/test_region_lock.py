"""Tests for generalized region-lock detection."""

from app.collectors.base import RawJob
from app.pipeline.location import detect_region_lock, tag_location

EXCLUDED = ["india", "pakistan", "bangladesh", "israel"]


def _job(location="", description="") -> RawJob:
    return RawJob(source="t", source_url="u", company="c", title="QA Engineer",
                  location=location, description=description)


def test_uae_only_detected():
    job = _job(description="Applicants from UAE only. Mobile games QA.")
    assert detect_region_lock(job) == "uae"
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_must_reside_in_germany():
    job = _job(description="Candidates must reside in Germany for this role.")
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_philippines_only_applicants():
    job = _job(description="Only open to applicants from the Philippines.")
    assert detect_region_lock(job) == "philippines"


def test_region_lock_with_relocation_is_viable():
    job = _job(description="Applicants from UAE only. Full relocation package and visa sponsorship provided.")
    assert tag_location(job, EXCLUDED) == "RELOCATION"


def test_egypt_lock_is_home_not_lockout():
    job = _job(description="Applicants from Egypt only.")
    assert detect_region_lock(job) is None
    assert tag_location(job, EXCLUDED) == "EGYPT_LISTED"


def test_open_remote_not_locked():
    job = _job(location="Worldwide", description="Work from anywhere, async team.")
    assert detect_region_lock(job) is None
    assert tag_location(job, EXCLUDED) == "ANYWHERE"


def test_generic_location_mention_not_a_lock():
    job = _job(description="Our HQ is in France, but the team is fully remote worldwide.")
    assert detect_region_lock(job) is None
