"""Configuration loading: config.yml + profile/cv_profile.yml + .env.

Everything the pipeline needs is loaded once at startup and re-read
per scan for the profile (so profile edits apply without restart).

The committed `profile/cv_profile.yml` is example data. Put a real CV in
`profile/cv_profile.local.yml` (gitignored) or set JOBHUNT_PROFILE to keep
personal data out of version control.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]  # repo root
CONFIG_PATH = ROOT / "config.yml"
PROFILE_DIR = ROOT / "profile"
# `profile/cv_profile.yml` is the committed example. A real CV is personal data,
# so it belongs in a gitignored `profile/cv_profile.local.yml` (or whatever
# JOBHUNT_PROFILE points at) and is preferred whenever it exists.
PROFILE_LOCAL_PATH = PROFILE_DIR / "cv_profile.local.yml"
PROFILE_EXAMPLE_PATH = PROFILE_DIR / "cv_profile.yml"


def _resolve_profile_path() -> Path:
    """Local private profile wins; fall back to the committed example."""
    override = os.environ.get("JOBHUNT_PROFILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return PROFILE_LOCAL_PATH if PROFILE_LOCAL_PATH.exists() else PROFILE_EXAMPLE_PATH


PROFILE_PATH = _resolve_profile_path()
COMPANIES_PATH = ROOT / "companies.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


@dataclass
class Settings:
    """Environment-backed secrets and host settings."""

    discord_webhook_url: str = ""
    discord_bot_token: str = ""
    openrouter_api_key: str = ""
    agnes_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "hydrahunt/0.1"
    port: int = 8420
    host: str = "127.0.0.1"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        load_dotenv(env_file or ROOT / ".env")
        return cls(
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            agnes_api_key=os.getenv("AGNES_API_KEY", ""),
            reddit_client_id=os.getenv("REDDIT_CLIENT_ID", ""),
            reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
            reddit_user_agent=os.getenv("REDDIT_USER_AGENT", "hydrahunt/0.1"),
            port=int(os.getenv("JOBHUNT_PORT", "8420")),
            host=os.getenv("JOBHUNT_HOST", "127.0.0.1"),
        )


@dataclass
class AppConfig:
    """Merged runtime configuration."""

    raw: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)
    companies: dict[str, Any] = field(default_factory=dict)
    settings: Settings = field(default_factory=Settings)

    @property
    def db_path(self) -> Path:
        """Absolute DB path, resolved against repo root."""
        raw_path = Path(self.raw.get("app", {}).get("db_path", "data/jobhunt.db"))
        return raw_path if raw_path.is_absolute() else ROOT / raw_path

    @property
    def excluded_regions(self) -> list[str]:
        loc = self.profile.get("location", {})
        return [r.lower() for r in loc.get("excluded_regions", [])]

    @property
    def target_titles(self) -> list[str]:
        return self.profile.get("target_titles", [])

    @property
    def title_blocklist(self) -> list[str]:
        return self.profile.get("title_blocklist", [])

    @property
    def skills(self) -> dict[str, list[str]]:
        return self.profile.get("skills", {})

    @property
    def company_blacklist(self) -> set[str]:
        return {c.lower() for c in self.companies.get("blacklist", [])}

    @property
    def company_us_only(self) -> set[str]:
        """Employers that only hire inside the US (user rule: US-remote is BS)."""
        return {c.lower() for c in self.companies.get("us_only", [])}

    def collectors_enabled(self, name: str) -> bool:
        return bool(self.raw.get("collectors", {}).get(name, {}).get("enabled", False))


def load_config() -> AppConfig:
    """Load all configuration from disk (fresh read on every call)."""
    cfg = AppConfig(
        raw=_load_yaml(CONFIG_PATH),
        profile=_load_yaml(PROFILE_PATH),
        companies=_load_yaml(COMPANIES_PATH),
        settings=Settings.from_env(),
    )
    # config.yml app.port/app.host as defaults; env vars take precedence
    app_section = cfg.raw.get("app", {})
    if "JOBHUNT_PORT" not in os.environ and app_section.get("port"):
        cfg.settings.port = int(app_section["port"])
    if "JOBHUNT_HOST" not in os.environ and app_section.get("host"):
        cfg.settings.host = str(app_section["host"])
    return cfg
