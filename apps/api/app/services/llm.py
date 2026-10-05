"""Provider-neutral LLM boundary for media intent extraction and chat replies.

Providers return data only. They cannot submit automation requests or receive
any credentials other than their own API key.
"""

import json
import time
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
class ReadAction:
    kind: str
    sql: str | None = None
    service: str | None = None
    query: str | None = None


@dataclass(frozen=True)
class AgentReply:
    message: str
    intent: MediaIntent | None = None
    read_action: ReadAction | None = None
    delete_intent: MediaIntent | None = None
    stop_intent: MediaIntent | None = None
    stop_mode: str | None = None


class IntentProvider(Protocol):
    def extract_media_intent(self, message: str) -> MediaIntent: ...

    def reply(self, message: str, history: list[tuple[str, str]]) -> AgentReply: ...

    def reply_with_tool_result(self, message: str, history: list[tuple[str, str]], result: object) -> str: ...


class RuleBasedIntentProvider:
    def extract_media_intent(self, message: str) -> MediaIntent:
        command, _, title = message.strip().partition(" ")
        media_type = {"/movie": MediaType.MOVIE, "/tv": MediaType.TV}.get(command.lower())
        if media_type is None or not title.strip():
            raise RequestError("Use /movie or /tv followed by a title, or configure an LLM provider.")
        return MediaIntent(title=title.strip(), media_type=media_type)

    def reply(self, message: str, history: list[tuple[str, str]]) -> AgentReply:
        try:
            return AgentReply("I can help with movie and TV requests. Try /movie or /tv.", self.extract_media_intent(message))
        except RequestError:
            return AgentReply("I can help with movie and TV requests. Try /movie or /tv.")

    def reply_with_tool_result(self, message: str, history: list[tuple[str, str]], result: object) -> str:
        return "I can’t answer that status question without Gemini configured."


class GeminiIntentProvider:
    """Gemini Developer API adapter with a deliberately narrow output contract."""

    def __init__(self, api_key: str, model: str = "gemini-3.1-flash-lite"):
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
        response = None
        last_error: httpx.HTTPError | None = None
        for attempt in range(3):
            try:
                response = httpx.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": self.api_key},
                    json=payload,
                    timeout=45,
                )
                if getattr(response, "status_code", 200) not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    break
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if attempt == 2 or (status is not None and status not in {429, 500, 502, 503, 504}):
                    break
                time.sleep(0.5 * (attempt + 1))
        if response is None or last_error is not None and getattr(response, "status_code", 500) >= 400:
            exc = last_error or httpx.HTTPError("Gemini did not return a response")
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
            action = None
            if data.get("read_action") is not None:
                action_data = data["read_action"]
                if not isinstance(action_data, dict) or action_data.get("kind") not in {
                    "database", "service", "requests", "downloads"
                }:
                    raise ValueError("invalid read action")
                action = ReadAction(
                    action_data["kind"],
                    action_data.get("sql"),
                    action_data.get("service"),
                    action_data.get("query"),
                )
            delete_intent = None
            delete_title = data.get("delete_title")
            delete_media_type = data.get("delete_media_type")
            if delete_title is not None or delete_media_type is not None:
                if (
                    not isinstance(delete_title, str)
                    or not delete_title.strip()
                    or delete_media_type not in {"movie", "tv"}
                ):
                    raise ValueError("incomplete deletion intent")
                delete_intent = MediaIntent(
                    delete_title.strip(), MediaType(delete_media_type), data.get("delete_year")
                )
            stop_intent = None
            stop_title = data.get("stop_title")
            stop_media_type = data.get("stop_media_type")
            stop_mode = data.get("stop_mode")
            if stop_title is not None or stop_media_type is not None or stop_mode is not None:
                if (
                    not isinstance(stop_title, str)
                    or not stop_title.strip()
                    or stop_media_type not in {"movie", "tv"}
                    or stop_mode not in {"download", "seeding"}
                ):
                    raise ValueError("incomplete stop intent")
                stop_intent = MediaIntent(
                    stop_title.strip(), MediaType(stop_media_type), data.get("stop_year")
                )
            if sum(value is not None for value in (intent, action, delete_intent, stop_intent)) > 1:
                raise ValueError("response requested multiple actions")
            return AgentReply(reply, intent, action, delete_intent, stop_intent, stop_mode)
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
                "delete_title": {"type": ["string", "null"]},
                "delete_media_type": {"type": ["string", "null"], "enum": ["movie", "tv", None]},
                "delete_year": {"type": ["integer", "null"]},
                "stop_title": {"type": ["string", "null"]},
                "stop_media_type": {"type": ["string", "null"], "enum": ["movie", "tv", None]},
                "stop_year": {"type": ["integer", "null"]},
                "stop_mode": {"type": ["string", "null"], "enum": ["download", "seeding", None]},
                "read_action": {"type": ["object", "null"], "properties": {"kind": {"type": "string", "enum": ["database", "service", "requests", "downloads"]}, "sql": {"type": ["string", "null"]}, "service": {"type": ["string", "null"], "enum": ["radarr", "sonarr", "qbittorrent", "jellyfin", None]}, "query": {"type": ["string", "null"]}}, "required": ["kind", "sql", "service", "query"], "additionalProperties": False},
            },
            "required": ["message", "title", "media_type", "year", "delete_title", "delete_media_type", "delete_year", "stop_title", "stop_media_type", "stop_year", "stop_mode", "read_action"],
            "additionalProperties": False,
        }

    @staticmethod
    def _instructions() -> str:
        return (
            "You are a concise household movie and TV assistant. Help with film and TV recommendations, "
            f"cast-based suggestions, and current {settings.media_market_country} streaming availability. Use web search when a "
            "question depends on current release or streaming information. Never claim availability "
            "without checking current sources. You cannot directly download, submit, or delete anything; "
            "application code performs confirmed actions. "
            "When the user clearly asks to add one specific movie or TV series, return its normalized "
            "title, media type, and year if known. When the user asks to remove/delete one specific item from "
            "the library or hard drive, set delete_title, delete_media_type, and delete_year instead; deletion "
            "will require a separate confirmation. When the user asks to cancel/stop an unfinished download, "
            "set stop_title, stop_media_type, stop_year, and stop_mode='download'. When they ask to stop seeding "
            "a completed item, use stop_mode='seeding'; this keeps the imported library item. Resolve short "
            "follow-ups such as 'stop seeding' from the conversation history. If the title is still ambiguous, "
            "ask for it and leave all action fields null. Stop actions require a separate confirmation. "
            "For a request/download status question use read_action kind "
            "requests with the title as query. For active downloads or progress use kind downloads. For other "
            "database questions, return one SELECT on media_requests or chat_messages; for service health return "
            "one named service. Choose at most one action. Set all unused action fields to null. Keep replies brief."
        )

    def reply_with_tool_result(self, message: str, history: list[tuple[str, str]], result: object) -> str:
        # Tool data is bounded and generated by application code, never by another user.
        prompt = f"Question: {message}\nTrusted read-only tool result: {json.dumps(result, default=str)}\nAnswer concisely; do not mention credentials."
        return self._respond(prompt, history).message


def get_intent_provider() -> IntentProvider:
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return GeminiIntentProvider(settings.gemini_api_key, settings.gemini_model)
    return RuleBasedIntentProvider()
