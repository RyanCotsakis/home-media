"""Provider-neutral LLM boundary for media intent extraction and chat replies.

Providers return data only. They cannot submit automation requests or receive
any credentials other than their own API key.
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


@dataclass(frozen=True)
class AgentReply:
    message: str
    intent: MediaIntent | None = None


class IntentProvider(Protocol):
    def extract_media_intent(self, message: str) -> MediaIntent: ...

    def reply(self, message: str, history: list[tuple[str, str]]) -> AgentReply: ...


class RuleBasedIntentProvider:
    def extract_media_intent(self, message: str) -> MediaIntent:
        command, _, title = message.strip().partition(" ")
        media_type = {"/movie": MediaType.MOVIE, "/tv": MediaType.TV}.get(command.lower())
        if media_type is None or not title.strip():
            raise RequestError("Use /movie Title or /tv Title, or configure an LLM provider.")
        return MediaIntent(title=title.strip(), media_type=media_type)

    def reply(self, message: str, history: list[tuple[str, str]]) -> AgentReply:
        try:
            return AgentReply("I can help with movie and TV requests. Try /movie Title or /tv Title.", self.extract_media_intent(message))
        except RequestError:
            return AgentReply("I can help with movie and TV requests. Try /movie Title or /tv Title.")


class GeminiIntentProvider:
    """Gemini Developer API adapter with a deliberately narrow output contract."""

    def __init__(self, api_key: str, model: str = "gemini-flash-latest"):
        self.api_key = api_key
        self.model = model

    def extract_media_intent(self, message: str) -> MediaIntent:
        reply = self._respond(message, [])
        if reply.intent is None:
            raise RequestError("Please name a movie or TV show to request.")
        return reply.intent

    def reply(self, message: str, history: list[tuple[str, str]]) -> AgentReply:
        return self._respond(message, history)

    def _respond(self, message: str, history: list[tuple[str, str]]) -> AgentReply:
        contents = [
            {"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]}
            for role, content in history
            if role in {"user", "assistant"}
        ]
        contents.append({"role": "user", "parts": [{"text": message}]})
        payload: dict[str, object] = {
            "system_instruction": {"parts": [{"text": self._instructions()}]},
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": self._response_schema(),
            },
        }
        if settings.gemini_google_search_enabled:
            payload["tools"] = [{"googleSearch": {}}]
        try:
            response = httpx.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key},
                json=payload,
                timeout=45,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RequestError("The assistant is temporarily unavailable. Please try again shortly.") from exc
        try:
            data = json.loads(response.json()["candidates"][0]["content"]["parts"][0]["text"])
            title = data["title"]
            media_type = data["media_type"]
            intent = None
            if title is not None or media_type is not None:
                if not isinstance(title, str) or not title.strip() or media_type not in {"movie", "tv"}:
                    raise ValueError("incomplete media intent")
                intent = MediaIntent(title=title.strip(), media_type=MediaType(media_type), year=data["year"])
            reply = data["message"].strip()
            if not reply:
                raise ValueError("empty assistant response")
            return AgentReply(reply, intent)
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RequestError("The assistant returned an invalid response. Please try again.") from exc

    @staticmethod
    def _response_schema() -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "title": {"type": ["string", "null"]},
                "media_type": {"type": ["string", "null"], "enum": ["movie", "tv", None]},
                "year": {"type": ["integer", "null"]},
            },
            "required": ["message", "title", "media_type", "year"],
            "additionalProperties": False,
        }

    @staticmethod
    def _instructions() -> str:
        return (
            "You are a concise household media assistant. Help with film and TV recommendations, "
            f"cast-based suggestions, and current {settings.media_market_country} streaming availability. Use web search when a "
            "question depends on current release or streaming information. Never claim availability "
            "without checking current sources. You cannot download, submit, or modify anything. "
            "When the user clearly asks to obtain one specific movie or TV series, return its normalized "
            "title, media type, and year if known; otherwise set those fields to null. For every other "
            "message, set title, media_type, and year to null. Keep the response friendly and brief."
        )


def get_intent_provider() -> IntentProvider:
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return GeminiIntentProvider(settings.gemini_api_key, settings.gemini_model)
    return RuleBasedIntentProvider()
