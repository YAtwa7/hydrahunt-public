"""Regression: INC-003 — null content from a free model must not crash scoring."""

from unittest.mock import MagicMock, patch

from app.scoring.llm import _call_provider

PROVIDER = {"name": "openrouter", "base_url": "https://x", "timeout_s": 5}


def _mock_response(content):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def test_null_content_returns_none_not_crash():
    with patch("app.scoring.llm.httpx.post", return_value=_mock_response(None)):
        assert _call_provider(PROVIDER, "key", "model", "prompt") is None


def test_valid_json_content_parsed():
    with patch(
        "app.scoring.llm.httpx.post",
        return_value=_mock_response('{"score": 80, "tier": "A"}'),
    ):
        assert _call_provider(PROVIDER, "key", "model", "prompt") == {"score": 80, "tier": "A"}


def test_garbage_content_returns_none():
    with patch("app.scoring.llm.httpx.post", return_value=_mock_response("not json")):
        assert _call_provider(PROVIDER, "key", "model", "prompt") is None


def test_http_error_returns_none():
    resp = MagicMock()
    resp.status_code = 429
    with patch("app.scoring.llm.httpx.post", return_value=resp):
        assert _call_provider(PROVIDER, "key", "model", "prompt") is None


def test_network_exception_returns_none():
    with patch("app.scoring.llm.httpx.post", side_effect=RuntimeError("boom")):
        assert _call_provider(PROVIDER, "key", "model", "prompt") is None
