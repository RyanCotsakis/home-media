import pytest

from app.models import MediaType
from app.services.llm import RuleBasedIntentProvider
from app.services.requests import RequestError


def test_rule_based_provider_returns_only_typed_intent() -> None:
    intent = RuleBasedIntentProvider().extract_media_intent("/tv The Bear")
    assert intent.title == "The Bear"
    assert intent.media_type is MediaType.TV


def test_rule_based_provider_rejects_unconstrained_command() -> None:
    with pytest.raises(RequestError):
        RuleBasedIntentProvider().extract_media_intent("download something")
