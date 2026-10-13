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


def test_env_example_placeholders_read_as_unset(monkeypatch):
    """Copying .env.example to .env verbatim must not look like real config.

    `.env.example` ships DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
    That is non-empty, so every `if not value` guard passes and the notifier
    POSTs to discord.com on every scan (405), then stamps notified_at because
    the single-attempt sender treats any HTTP response as delivered.
    """
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/...")
    monkeypatch.setenv("OPENROUTER_API_KEY", "...")
    monkeypatch.setenv("REDDIT_USER_AGENT", "qa-job-hunter/0.1 by u/YOUR_USERNAME")

    cfg = load_config()
    assert cfg.settings.discord_webhook_url == ""
    assert cfg.settings.openrouter_api_key == ""
    # the user agent has a usable default instead of going blank
    assert "YOUR_USERNAME" not in cfg.settings.reddit_user_agent


def test_real_values_survive_env_cleaning(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/abc")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-realkey")
    cfg = load_config()
    assert cfg.settings.discord_webhook_url.endswith("123/abc")
    assert cfg.settings.openrouter_api_key == "sk-or-v1-realkey"
