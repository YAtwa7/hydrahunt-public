"""Regression tests — title policy: software QA in; automation/non-software out.

Policy (v0.2.0): SDET = automation engineer = rejected. Manufacturing,
hardware, Linux/IoT, call-center/CX QA rejected. Manual/software QA
mutations accepted.
"""

import pytest

from app.collectors.base import RawJob
from app.config import load_config
from app.pipeline.filters import HardFilter

PASS_TITLES = [
    "QA Engineer",
    "Manual QA Engineer",
    "Software QA Engineer",
    "Software Test Engineer",
    "Test Analyst",
    "QA Tester",
    "Software Quality Engineer",
    "AI Quality Engineer",
]

REJECT_TITLES = [
    "SDET II",                                        # automation engineer
    "QA Automation Engineer",                         # automation-focused
    "Test Infrastructure Engineer",                   # automation/infra
    "Software Quality Assurance Engineer - Linux, PC, IoT",  # Linux/IoT
    "Manufacturing Quality Engineer, Data Center",    # not software
    "Call Center QA Analyst",                         # CX quality
    "Customer Experience QA",                         # CX quality
    # industrial / construction / compliance QA (v0.5.7): no longer targets
    "QC Engineer",
    "QA/QC Engineer",
    "Product Quality Engineer",
    "QA/QC",
    "QAQC",
]


@pytest.mark.parametrize("title", PASS_TITLES)
def test_software_qa_titles_pass(title):
    hf = HardFilter(load_config())
    job = RawJob(source="t", source_url="u", company="Acme", title=title)
    assert hf.reject_reason(job) is None, f"{title!r} should pass"


@pytest.mark.parametrize("title", REJECT_TITLES)
def test_non_target_titles_rejected(title):
    hf = HardFilter(load_config())
    job = RawJob(source="t", source_url="u", company="Acme", title=title)
    assert hf.reject_reason(job) is not None, f"{title!r} should be rejected"
