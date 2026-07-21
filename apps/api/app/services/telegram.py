"""Telegram long-polling adapter; no public webhook endpoint is required."""

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MediaType
from app.services.chat_history import add_message, load_history
from app.services.llm import get_intent_provider
from app.services.automation import get_automation_client
from app.services.requests import RequestError, confirm_request, create_pending_request

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, token: str):
        self.base_url = f"https://api.telegram.org/bot{token}"

    def get_updates(self, offset: int | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": 1, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        response = httpx.post(f"{self.base_url}/getUpdates", json=payload, timeout=10)
        response.raise_for_status()
        return response.json().get("result", [])

    def send_message(self, chat_id: int, text: str, confirmation_token: str | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if confirmation_token:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": "Confirm request", "callback_data": f"confirm:{confirmation_token}"}]]
            }
        response = httpx.post(f"{self.base_url}/sendMessage", json=payload, timeout=10)
        response.raise_for_status()

    def answer_callback(self, callback_id: str, text: str) -> None:
        response = httpx.post(
            f"{self.base_url}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": text}, timeout=10
        )
        response.raise_for_status()


def allowed(telegram_user_id: int) -> bool:
    return telegram_user_id in settings.telegram_allowed_user_ids


def parse_command(text: str) -> tuple[MediaType, str]:
    intent = get_intent_provider().extract_media_intent(text)
    return intent.media_type, intent.title


def process_chat_message(db: Session, client: TelegramClient, *, sender: int, chat_id: int, text: str) -> None:
    history = load_history(db, chat_id)
    provider = get_intent_provider()
    reply = provider.reply(text, history)
    add_message(db, chat_id, "user", text)
    if reply.intent is None:
        add_message(db, chat_id, "assistant", reply.message)
        client.send_message(chat_id, reply.message)
        return
    request = create_pending_request(
        db,
        get_automation_client(),
        requester_id=sender,
        chat_id=chat_id,
        title=reply.intent.title,
        media_type=reply.intent.media_type,
        year=reply.intent.year,
    )
    response = f"{reply.message}\n\nI found {request.title} ({request.media_type.value}). Download it?"
    add_message(db, chat_id, "assistant", response)
    client.send_message(chat_id, response, request.confirmation_token)


def process_update(db: Session, client: TelegramClient, update: dict[str, Any]) -> None:
    """Process one Telegram update; authorization is always checked before action."""
    if message := update.get("message"):
        sender = message["from"]["id"]
        chat_id = message["chat"]["id"]
        if not allowed(sender):
            client.send_message(chat_id, "This bot is restricted to the household allowlist.")
            return
        try:
            process_chat_message(db, client, sender=sender, chat_id=chat_id, text=message.get("text", ""))
        except RequestError as exc:
            client.send_message(chat_id, str(exc))
        except Exception:
            db.rollback()
            logger.exception("Failed to create a media request")
            client.send_message(chat_id, "I couldn't create that request. Please try again shortly.")
        return

    callback = update.get("callback_query")
    if not callback:
        return
    sender = callback["from"]["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    if not allowed(sender) or not data.startswith("confirm:"):
        client.answer_callback(callback["id"], "Not authorized.")
        return
    try:
        request = confirm_request(
            db, get_automation_client(), requester_id=sender, confirmation_token=data.removeprefix("confirm:")
        )
        client.answer_callback(callback["id"], "Request submitted.")
        client.send_message(chat_id, f"{request.title} was submitted. I’ll let you know when it is ready.")
    except RequestError as exc:
        client.answer_callback(callback["id"], str(exc))
