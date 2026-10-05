"""Telegram long-polling adapter; no public webhook endpoint is required."""

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MediaType
from app.services.chat_history import add_message, load_history
from app.services.deletions import confirm_deletion, create_pending_deletion
from app.services.llm import MediaIntent, get_intent_provider
from app.services.automation import get_automation_client
from app.services.requests import RequestError, confirm_request, create_pending_request
from app.services.stops import confirm_stop, create_pending_stop
from app.services.read_tools import (
    ReadToolError,
    ToolResult,
    active_downloads,
    request_status_with_downloads,
    run_database_query,
    service_status,
)

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

    def send_message(
        self,
        chat_id: int,
        text: str,
        confirmation_token: str | None = None,
        *,
        confirmation_action: str = "confirm",
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if confirmation_token:
            button_labels = {
                "delete": "Delete files",
                "stop": "Stop and remove download",
            }
            payload["reply_markup"] = {
                "inline_keyboard": [[{
                    "text": button_labels.get(confirmation_action, "Confirm request"),
                    "callback_data": f"{confirmation_action}:{confirmation_token}",
                }]]
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


def format_tool_result(result: ToolResult) -> str:
    if result.kind == "downloads":
        downloads = result.data if isinstance(result.data, list) else []
        if not downloads:
            return "There are no active movie or TV downloads."
        lines = ["Active downloads:"]
        for item in downloads:
            if not isinstance(item, dict):
                continue
            speed = int(item.get("download_speed_bytes_per_second") or 0) / 1_000_000
            suffix = f", {speed:.1f} MB/s" if speed else ""
            total_size = int(item.get("total_size_bytes") or 0)
            size = f", {total_size / 1_000_000_000:.2f} GB" if total_size else ""
            resolution = f", {item.get('resolution')}" if item.get("resolution") else ""
            lines.append(
                f"• {item.get('name')}: {item.get('percent_complete')}% "
                f"({item.get('state')}{suffix}{resolution}{size})"
            )
        return "\n".join(lines)
    if result.kind == "requests":
        requests = result.data if isinstance(result.data, list) else (
            result.data.get("requests", []) if isinstance(result.data, dict) else []
        )
        if not requests:
            return "I couldn't find a matching request."
        lines = ["Request status:"]
        for item in requests:
            if isinstance(item, dict):
                lines.append(f"• {item.get('title')}: {str(item.get('stage')).replace('_', ' ')}")
        if isinstance(result.data, dict) and result.data.get("download_status_available"):
            downloads = result.data.get("active_downloads", [])
            if not downloads:
                lines.append("No matching download is active in qBittorrent.")
        return "\n".join(lines)
    return f"Status result: {result.data}"


def begin_deletion(
    db: Session,
    client: TelegramClient,
    *,
    sender: int,
    chat_id: int,
    intent: MediaIntent,
) -> None:
    deletion = create_pending_deletion(
        db,
        get_automation_client(),
        requester_id=sender,
        chat_id=chat_id,
        title=intent.title,
        media_type=intent.media_type,
        year=intent.year,
    )
    year = f" ({deletion.year})" if deletion.year else ""
    response = (
        f"I found {deletion.title}{year} in the {deletion.media_type.value} library. "
        "Any matching active torrent will be stopped and verified removed first. "
        "Then delete it and its files from the hard drive? This cannot be undone."
    )
    add_message(db, chat_id, "assistant", response)
    client.send_message(
        chat_id,
        response,
        deletion.confirmation_token,
        confirmation_action="delete",
    )


def process_chat_message(db: Session, client: TelegramClient, *, sender: int, chat_id: int, text: str) -> None:
    history = load_history(db, chat_id)
    command, _, argument = text.strip().partition(" ")
    if command.lower() in {"/downloads", "/status"}:
        result = active_downloads() if command.lower() == "/downloads" else request_status_with_downloads(
            db, requester_id=sender, query=argument or None
        )
        response = format_tool_result(result)
        add_message(db, chat_id, "user", text)
        add_message(db, chat_id, "assistant", response)
        client.send_message(chat_id, response)
        return
    if command.lower() == "/stop":
        add_message(db, chat_id, "user", text)
        if not argument.strip():
            result = active_downloads()
            response = f"{format_tool_result(result)}\n\nUse /stop Title to cancel one download."
            add_message(db, chat_id, "assistant", response)
            client.send_message(chat_id, response)
            return
        stop, request = create_pending_stop(
            db, requester_id=sender, chat_id=chat_id, title=argument
        )
        service = "Radarr" if request.media_type is MediaType.MOVIE else "Sonarr"
        response = (
            f"Stop {request.title}? This will unmonitor it, cancel it in "
            f"{service}, and remove its partial torrent data from qBittorrent."
        )
        add_message(db, chat_id, "assistant", response)
        client.send_message(
            chat_id,
            response,
            stop.confirmation_token,
            confirmation_action="stop",
        )
        return
    if command.lower() == "/delete":
        raw_type, _, title = argument.partition(" ")
        media_type = {"movie": MediaType.MOVIE, "tv": MediaType.TV}.get(raw_type.lower())
        if media_type is None or not title.strip():
            raise RequestError("Use /delete movie Title or /delete tv Title.")
        add_message(db, chat_id, "user", text)
        begin_deletion(
            db,
            client,
            sender=sender,
            chat_id=chat_id,
            intent=MediaIntent(title.strip(), media_type),
        )
        return
    provider = get_intent_provider()
    reply = provider.reply(text, history)
    add_message(db, chat_id, "user", text)
    if reply.read_action is not None:
        if reply.intent is not None:
            raise RequestError("Please make one request at a time.")
        try:
            if reply.read_action.kind == "database" and reply.read_action.sql:
                result = run_database_query(reply.read_action.sql, requester_id=sender, chat_id=chat_id)
            elif reply.read_action.kind == "service" and reply.read_action.service:
                result = service_status(reply.read_action.service)
            elif reply.read_action.kind == "requests":
                result = request_status_with_downloads(
                    db, requester_id=sender, query=reply.read_action.query
                )
            elif reply.read_action.kind == "downloads":
                result = active_downloads()
            else:
                raise ReadToolError("The requested read operation was incomplete.")
            try:
                response = provider.reply_with_tool_result(text, history, result.data)
            except RequestError:
                response = format_tool_result(result)
            if (
                result.kind == "requests"
                and isinstance(result.data, dict)
                and result.data.get("download_status_available")
                and not result.data.get("active_downloads")
            ):
                response = f"{response}\nNo matching download is currently active in qBittorrent."
        except (ReadToolError, httpx.HTTPError) as exc:
            response = f"I couldn’t complete that read-only check: {exc}"
        add_message(db, chat_id, "assistant", response)
        client.send_message(chat_id, response)
        return
    if reply.delete_intent is not None:
        begin_deletion(
            db,
            client,
            sender=sender,
            chat_id=chat_id,
            intent=reply.delete_intent,
        )
        return
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
    response = f"{reply.message}\n\nI found {request.title} ({request.media_type.value}). Add it to your library?"
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
    if not allowed(sender) or not data.startswith(("confirm:", "delete:", "stop:")):
        client.answer_callback(callback["id"], "Not authorized.")
        return
    try:
        if data.startswith("stop:"):
            stop, request = confirm_stop(
                db,
                get_automation_client(),
                requester_id=sender,
                confirmation_token=data.removeprefix("stop:"),
            )
            client.answer_callback(callback["id"], "Download stopped and removed.")
            client.send_message(
                chat_id,
                f"{request.title} was stopped. Removed {stop.stopped_items} active download item(s).",
            )
            return
        if data.startswith("delete:"):
            deletion = confirm_deletion(
                db,
                get_automation_client(),
                requester_id=sender,
                confirmation_token=data.removeprefix("delete:"),
            )
            client.answer_callback(callback["id"], "Deleted from library and disk.")
            client.send_message(chat_id, f"{deletion.title} was deleted from the library and hard drive.")
            return
        request = confirm_request(
            db, get_automation_client(), requester_id=sender, confirmation_token=data.removeprefix("confirm:")
        )
        client.answer_callback(callback["id"], "Request submitted.")
        client.send_message(chat_id, f"{request.title} was submitted. I’ll let you know when it is ready.")
    except RequestError as exc:
        client.answer_callback(callback["id"], str(exc))
        client.send_message(chat_id, f"I couldn't complete that action: {exc}")
