"""Tests for location feasibility tagging."""

from app.collectors.base import RawJob
from app.pipeline.location import eor_friendly, tag_location

EXCLUDED = ["india", "pakistan", "bangladesh"]


def _job(location="", description="") -> RawJob:
    return RawJob(source="t", source_url="u", company="c", title="QA Engineer",
                  location=location, description=description)


def test_anywhere():
    assert tag_location(_job(location="Worldwide", description="Work from anywhere."), EXCLUDED) == "ANYWHERE"


def test_emea():
    job = _job(location="Remote - EMEA", description="CET timezone ±2h.")
    assert tag_location(job, EXCLUDED) == "EMEA_OK"


def test_egypt():
    job = _job(description="Open to candidates in Egypt or MENA region.")
    assert tag_location(job, EXCLUDED) == "EGYPT_LISTED"


def test_relocation():
    job = _job(location="Berlin", description="We offer relocation support and visa sponsorship.")
    assert tag_location(job, EXCLUDED) == "RELOCATION"


def test_excluded():
    assert tag_location(_job(location="India"), EXCLUDED) == "EXCLUDED"


def test_us_only():
    job = _job(description="Must reside in the US and be authorized to work in the U.S.")
    assert tag_location(job, EXCLUDED) == "EXCLUDED"


def test_hybrid():
    job = _job(location="London", description="Hybrid role, 3 days in-office per week.")
    assert tag_location(job, EXCLUDED) == "HYBRID_ONSITE"


def test_eor_counts_as_emea_ok_and_flags():
    job = _job(description="Hired via Deel as a contractor, work remotely.")
    assert tag_location(job, EXCLUDED) == "EMEA_OK"
    assert eor_friendly(job) is True


def test_unknown():
    job = _job(location="Remote", description="Great team, great product.")
    assert tag_location(job, EXCLUDED) == "UNKNOWN"
