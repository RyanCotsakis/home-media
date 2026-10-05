import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MediaRequest, MediaStop, RequestStatus
from app.services.automation import AutomationClient
from app.services.requests import RequestError


def create_pending_stop(
    db: Session,
    *,
    requester_id: int,
    chat_id: int,
    title: str,
) -> tuple[MediaStop, MediaRequest]:
    request = db.scalar(
        select(MediaRequest)
        .where(
            MediaRequest.requester_telegram_id == requester_id,
            MediaRequest.title.ilike(f"%{title.strip()}%"),
            MediaRequest.status.in_((RequestStatus.SUBMITTED, RequestStatus.DOWNLOADING)),
        )
        .order_by(MediaRequest.created_at.desc())
    )
    if request is None or request.automation_id is None:
        raise RequestError("I couldn't find a matching submitted or active request to stop.")
    existing = db.scalar(
        select(MediaStop)
        .where(MediaStop.request_id == request.id, MediaStop.status == "pending_confirmation")
        .order_by(MediaStop.created_at.desc())
    )
    if existing is not None:
        return existing, request
    stop = MediaStop(
        request_id=request.id,
        requester_telegram_id=requester_id,
        chat_id=chat_id,
        confirmation_token=secrets.token_urlsafe(24),
    )
    db.add(stop)
    db.commit()
    db.refresh(stop)
    return stop, request


def confirm_stop(
    db: Session,
    automation: AutomationClient,
    *,
    requester_id: int,
    confirmation_token: str,
) -> tuple[MediaStop, MediaRequest]:
    stop = db.scalar(select(MediaStop).where(MediaStop.confirmation_token == confirmation_token))
    if stop is None or stop.requester_telegram_id != requester_id:
        raise RequestError("Stop confirmation is invalid for this user.")
    if stop.status != "pending_confirmation":
        raise RequestError(f"Stop request is already {stop.status.replace('_', ' ')}.")
    request = db.get(MediaRequest, stop.request_id)
    if request is None or request.automation_id is None:
        raise RequestError("The associated media request no longer exists.")
    try:
        stopped = automation.stop_downloads(
            request.automation_id, request.media_type, title=request.title
        )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        stop.failure_reason = "The download could not be verified as stopped."
        db.commit()
        raise RequestError(
            "I could not verify that the torrent stopped, so no library files were touched."
        ) from exc
    stop.status = "stopped"
    stop.stopped_items = len(stopped)
    request.status = RequestStatus.STOPPED
    request.failure_reason = "Stopped by requester"
    db.commit()
    db.refresh(stop)
    db.refresh(request)
    return stop, request
