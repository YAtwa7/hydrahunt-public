"""Tests for config loading and profile-derived rules."""

import re

from app.config import load_config


def test_config_loads():
    cfg = load_config()
    assert cfg.raw, "config.yml should load"
    assert cfg.profile, "cv_profile.yml should load"


def test_excluded_regions_present():
    cfg = load_config()
    assert "india" in cfg.excluded_regions
    assert "pakistan" in cfg.excluded_regions


def test_target_titles_present():
    cfg = load_config()
    assert any("QA" in t for t in cfg.target_titles)


def test_skills_categories():
    cfg = load_config()
    for cat in ("core", "niche", "automation", "tools"):
        assert cat in cfg.skills, f"missing skill category {cat}"


def test_collectors_toggle():
    cfg = load_config()
    assert cfg.collectors_enabled("jobspy") is True


def test_reported_version_matches_the_changelog():
    """/healthz and the FastAPI app must not report a stale version.

    The version was hardcoded in three places and read 0.2.0 long after the
    project reached 0.5.x. Now there is a single source of truth, and a test
    that fails if CHANGELOG.md and __version__ drift apart.
    """
    from app import __version__
    from app.config import ROOT

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    latest = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    assert latest, "could not find a version heading in CHANGELOG.md"
    assert __version__ == latest.group(1), (
        f"__version__={__version__} but CHANGELOG says {latest.group(1)}"
    )


def test_healthz_reports_the_package_version():
    from fastapi.testclient import TestClient

    from app import __version__
    from app import main as main_mod

    body = TestClient(main_mod.app).get("/healthz").json()
    assert body["version"] == __version__
