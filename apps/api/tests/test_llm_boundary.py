import httpx
import pytest

from app.models import MediaType
from app.services.llm import GeminiIntentProvider, RuleBasedIntentProvider, get_intent_provider
from app.services.requests import RequestError


def test_rule_based_provider_returns_only_typed_intent() -> None:
    intent = RuleBasedIntentProvider().extract_media_intent("/tv The Bear")
    assert intent.title == "The Bear"
    assert intent.media_type is MediaType.TV


def test_rule_based_provider_rejects_unconstrained_command() -> None:
    with pytest.raises(RequestError):
        RuleBasedIntentProvider().extract_media_intent("download something")


def test_gemini_provider_sends_history_schema_and_validates_reply(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("app.services.llm.settings.gemini_google_search_enabled", True)

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"message":"Great choice.","title":"Arrival","media_type":"movie","year":2016}'}
                            ]
                        }
                    }
                ]
            }

    def fake_post(*args, **kwargs):
        captured["url"] = args[0]
        captured["headers"] = kwargs["headers"]
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.llm.httpx.post", fake_post)
    reply = GeminiIntentProvider("test-key", "test-model").reply("Get Arrival", [("user", "I like sci-fi"), ("assistant", "Try Arrival.")])

    assert reply.message == "Great choice."
    assert reply.intent is not None
    assert reply.intent.title == "Arrival"
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
    assert captured["headers"] == {"x-goog-api-key": "test-key"}
    assert any(item["parts"][0]["text"] == "I like sci-fi" for item in captured["contents"])
    assert any(item["role"] == "model" for item in captured["contents"])
    assert captured["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["tools"] == [{"googleSearch": {}}]


def test_gemini_provider_reports_invalid_response(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"candidates": []}

    monkeypatch.setattr("app.services.llm.httpx.post", lambda *args, **kwargs: Response())

    with pytest.raises(RequestError, match="invalid response"):
        GeminiIntentProvider("test-key").reply("Get Arrival", [])


def test_gemini_provider_reports_http_error(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "https://example.test")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)

    monkeypatch.setattr("app.services.llm.httpx.post", lambda *args, **kwargs: Response())

    with pytest.raises(RequestError, match="temporarily unavailable"):
        GeminiIntentProvider("test-key").reply("Get Arrival", [])


def test_gemini_provider_can_disable_google_search(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {
                "candidates": [
                    {"content": {"parts": [{"text": '{"message":"Hello","title":null,"media_type":null,"year":null}'}]}}
                ]
            }

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.llm.httpx.post", fake_post)
    monkeypatch.setattr("app.services.llm.settings.gemini_google_search_enabled", False)

    GeminiIntentProvider("test-key").reply("Hello", [])

    assert "tools" not in captured


def test_get_intent_provider_falls_back_without_gemini_key(monkeypatch) -> None:
    monkeypatch.setattr("app.services.llm.settings.llm_provider", "gemini")
    monkeypatch.setattr("app.services.llm.settings.gemini_api_key", None)

    assert isinstance(get_intent_provider(), RuleBasedIntentProvider)
