"""Provider-neutral, schema-validated intent extraction.

Providers return data only. They cannot invoke automation clients or receive
credentials beyond their own API key.
"""

import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import settings
from app.models import MediaType
from app.services.requests import RequestError


@dataclass(frozen=True)
class MediaIntent:
    title: str
    media_type: MediaType
    year: int | None = None


class IntentProvider(Protocol):
    def extract_media_intent(self, message: str) -> MediaIntent: ...


class RuleBasedIntentProvider:
    def extract_media_intent(self, message: str) -> MediaIntent:
        command, _, title = message.strip().partition(" ")
        media_type = {"/movie": MediaType.MOVIE, "/tv": MediaType.TV}.get(command.lower())
        if media_type is None or not title.strip():
            raise RequestError("Use /movie Title or /tv Title, or configure an LLM provider.")
        return MediaIntent(title=title.strip(), media_type=media_type)


class OpenAIIntentProvider:
    """Small Responses API adapter returning only a validated media intent."""

    def __init__(self, api_key: str, model: str = "gpt-4.1-mini"):
        self.api_key = api_key
        self.model = model

    def extract_media_intent(self, message: str) -> MediaIntent:
        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": message}],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "media_intent",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "media_type": {"type": "string", "enum": ["movie", "tv"]},
                            "year": {"type": ["integer", "null"]},
                        },
                        "required": ["title", "media_type", "year"],
                        "additionalProperties": False,
                    },
                }
            },
        }
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        try:
            data = json.loads(response.json()["output_text"])
            return MediaIntent(title=data["title"].strip(), media_type=MediaType(data["media_type"]), year=data["year"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RequestError("The intent provider returned an invalid media request") from exc


def get_intent_provider() -> IntentProvider:
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAIIntentProvider(settings.openai_api_key)
    return RuleBasedIntentProvider()
