import pytest

from app.models import MediaType
from app.services.llm import OpenAIIntentProvider, RuleBasedIntentProvider
from app.services.requests import RequestError


def test_rule_based_provider_returns_only_typed_intent() -> None:
    intent = RuleBasedIntentProvider().extract_media_intent("/tv The Bear")
    assert intent.title == "The Bear"
    assert intent.media_type is MediaType.TV


def test_rule_based_provider_rejects_unconstrained_command() -> None:
    with pytest.raises(RequestError):
        RuleBasedIntentProvider().extract_media_intent("download something")


def test_openai_provider_sends_history_and_validates_reply(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"output_text": '{"message":"Great choice.","title":"Arrival","media_type":"movie","year":2016}'}

    def fake_post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("app.services.llm.httpx.post", fake_post)
    reply = OpenAIIntentProvider("test-key", "test-model").reply("Get Arrival", [("user", "I like sci-fi")])

    assert reply.message == "Great choice."
    assert reply.intent is not None
    assert reply.intent.title == "Arrival"
    assert captured["model"] == "test-model"
    assert any(item["content"] == "I like sci-fi" for item in captured["input"])
