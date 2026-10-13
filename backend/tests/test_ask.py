"""Tests for the Ask HydraHunt grounded-Q&A endpoint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import ask
from app import main as main_mod
from app.config import AppConfig


def _cfg(**ask_cfg) -> AppConfig:
    """Minimal AppConfig with a fake LLM provider chain."""
    return AppConfig(
        raw={
            "ask": {"enabled": True, **ask_cfg},
            "collectors": {"remote_boards": {"enabled": True}, "jobspy": {"enabled": False}},
            "llm": {
                "providers": [
                    {"name": "openrouter", "base_url": "https://x.test/v1", "models": ["m1"]}
                ]
            },
        },
        profile={},
        companies={"companies": [{"name": "Acme"}]},
        settings=SimpleNamespace(openrouter_api_key="test-key", agnes_api_key=""),
    )


@pytest.fixture(autouse=True)
def _reset_shared_state():
    """Clear the rate limiter and the cross-test last-good-model memo."""
    ask._calls.clear()
    from app.scoring.llm import _last_good

    _last_good.clear()
    yield
    ask._calls.clear()
    _last_good.clear()


def test_empty_question_is_rejected():
    assert "error" in ask.ask("   ", _cfg())


def test_overlong_question_is_rejected():
    res = ask.ask("x" * (ask.MAX_QUESTION_CHARS + 1), _cfg())
    assert "too long" in res["error"]


def test_disabled_endpoint_returns_error(monkeypatch):
    cfg = _cfg(enabled=False)
    res = ask.ask("what is this?", cfg)
    assert "disabled" in res["error"]


def test_grounding_brief_is_present_and_readable():
    """The brief is the whole point - if it ships empty, answers are ungrounded."""
    text = ask._load_brief(ask.DEFAULT_MAX_CONTEXT_CHARS)
    assert "HydraHunt" in text
    assert "POOL_WORTHY_SQL" in text
    assert "remote_boards.py" in text


def test_brief_respects_context_cap():
    assert len(ask._load_brief(500)) <= 600


def test_answer_is_grounded_on_the_brief(monkeypatch):
    """The prompt we send must actually contain the brief."""
    seen: dict[str, str] = {}

    def fake_call(provider, api_key, model, prompt, system=None):
        seen["prompt"] = prompt
        seen["system"] = system or ""
        seen["model"] = model
        return {"answer": "The remote boards live in remote_boards.py."}

    monkeypatch.setattr(ask, "_call_provider", fake_call)
    res = ask.ask("where do remote jobs come from?", _cfg())

    assert res["answer"].startswith("The remote boards")
    assert res["model"] == "m1"
    assert "POOL_WORTHY_SQL" in seen["prompt"]        # brief was inlined
    assert "where do remote jobs come from" in seen["prompt"]  # question was inlined
    assert "STRICT JSON" in seen["system"]            # json_object mode is forced


def test_live_state_is_included(monkeypatch):
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        ask,
        "_call_provider",
        lambda p, k, m, prompt, system=None: (seen.update(prompt=prompt), {"answer": "ok"})[1],
    )
    ask.ask("what is enabled?", _cfg())
    assert "enabled collectors: remote_boards" in seen["prompt"]
    assert "jobspy" not in seen["prompt"].split("enabled collectors:")[1].split("\n")[0]


def test_provider_without_key_is_skipped(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        ask, "_call_provider", lambda p, k, m, prompt, system=None: calls.append(m) or None
    )
    cfg = AppConfig(
        raw={
            "ask": {"enabled": True},
            "llm": {"providers": [{"name": "openrouter", "base_url": "u", "models": ["m1"]}]},
        },
        profile={},
        companies={},
        settings=SimpleNamespace(openrouter_api_key="", agnes_api_key=""),
    )
    res = ask.ask("anything", cfg)
    assert calls == []          # no key -> never even called
    assert "no LLM provider answered" in res["error"]


def test_chain_falls_through_on_garbage(monkeypatch):
    """A malformed/empty reply must move to the next model, not raise."""
    calls: list[str] = []
    cfg = AppConfig(
        raw={
            "ask": {"enabled": True},
            "llm": {
                "providers": [
                    {"name": "openrouter", "base_url": "u", "models": ["bad", "good"]}
                ]
            },
        },
        profile={},
        companies={},
        settings=SimpleNamespace(openrouter_api_key="k", agnes_api_key=""),
    )

    def fake_call(provider, api_key, model, prompt, system=None):
        calls.append(model)
        return {"wrong_key": 1} if model == "bad" else {"answer": "recovered"}

    monkeypatch.setattr(ask, "_call_provider", fake_call)
    res = ask.ask("q", cfg)
    assert calls == ["bad", "good"]
    assert res["answer"] == "recovered"


def test_rate_limit_blocks_and_recovers(monkeypatch):
    monkeypatch.setattr(ask, "_call_provider", lambda *a, **k: {"answer": "ok"})
    cfg = _cfg(rate_limit_calls=2, rate_limit_window_s=3600)
    assert "answer" in ask.ask("one", cfg)
    assert "answer" in ask.ask("two", cfg)
    assert "rate limit" in ask.ask("three", cfg)["error"]

    # window expiry frees the budget
    import time as _t

    real = _t.monotonic
    monkeypatch.setattr(ask.time, "monotonic", lambda: real() + 4000)
    assert "answer" in ask.ask("four", cfg)


def test_missing_brief_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(ask, "BRIEF_PATH", tmp_path / "nope.md")
    res = ask.ask("q", _cfg())
    assert "brief missing" in res["error"]


def test_last_good_model_is_tried_first(monkeypatch):
    """Free models are slow; the memo must skip dead models ahead of a known-good."""
    calls: list[str] = []
    cfg = AppConfig(
        raw={
            "ask": {"enabled": True},
            "llm": {
                "providers": [
                    {
                        "name": "openrouter",
                        "base_url": "u",
                        "models": ["dead1", "good", "dead2"],
                    }
                ]
            },
        },
        profile={},
        companies={},
        settings=SimpleNamespace(openrouter_api_key="k", agnes_api_key=""),
    )
    def fake_call(provider, api_key, model, prompt, system=None):
        calls.append(model)
        return {"answer": "ok"} if model == "good" else None

    monkeypatch.setattr(ask, "_call_provider", fake_call)
    ask.ask("q", cfg)  # dead1 fails, good answers -> memo holds "good"
    assert calls == ["dead1", "good"]
    calls.clear()
    ask.ask("q2", cfg)
    assert calls == ["good"]  # "dead1" was not re-punched


def test_max_models_caps_the_chain(monkeypatch):
    """A long dead chain must not turn one request into minutes of timeouts."""
    calls: list[str] = []
    cfg = AppConfig(
        raw={
            "ask": {"enabled": True, "max_models": 2},
            "llm": {
                "providers": [
                    {"name": "openrouter", "base_url": "u", "models": ["a", "b", "c", "d"]}
                ]
            },
        },
        profile={},
        companies={},
        settings=SimpleNamespace(openrouter_api_key="k", agnes_api_key=""),
    )
    monkeypatch.setattr(
        ask, "_call_provider", lambda p, k, m, prompt, system=None: calls.append(m) or None
    )
    ask.ask("q", cfg)
    assert calls == ["a", "b"]


def test_endpoint_is_wired(monkeypatch):
    monkeypatch.setattr(main_mod, "load_config", lambda: _cfg())
    monkeypatch.setattr(ask, "_call_provider", lambda *a, **k: {"answer": "wired up"})
    client = TestClient(main_mod.app)

    ok = client.post("/api/ask", json={"question": "how does dedup work?"})
    assert ok.status_code == 200
    assert ok.json()["answer"] == "wired up"

    bad = client.post("/api/ask", json={"question": ""})
    assert bad.status_code == 200
    assert "error" in bad.json()
