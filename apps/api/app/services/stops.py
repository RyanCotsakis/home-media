import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MediaRequest, MediaStop, MediaType, RequestStatus
from app.services.automation import AutomationClient
from app.services.requests import RequestError


def create_pending_stop(
    db: Session,
    *,
    requester_id: int,
    chat_id: int,
    title: str,
    media_type: MediaType | None = None,
    seeding_only: bool = False,
) -> tuple[MediaStop, MediaRequest]:
    statement = select(MediaRequest).where(
        MediaRequest.requester_telegram_id == requester_id,
        MediaRequest.title.ilike(f"%{title.strip()}%"),
        MediaRequest.automation_id.is_not(None),
    )
    if media_type is not None:
        statement = statement.where(MediaRequest.media_type == media_type)
    if not seeding_only:
        statement = statement.where(
            MediaRequest.status.in_((RequestStatus.SUBMITTED, RequestStatus.DOWNLOADING))
        )
    request = db.scalar(statement.order_by(MediaRequest.created_at.desc()))
    if request is None or request.automation_id is None:
        action = "completed request to stop seeding" if seeding_only else "submitted or active request to stop"
        raise RequestError(f"I couldn't find a matching {action}.")
    pending_status = "pending_seeding_confirmation" if seeding_only else "pending_confirmation"
    existing = db.scalar(
        select(MediaStop)
        .where(MediaStop.request_id == request.id, MediaStop.status == pending_status)
        .order_by(MediaStop.created_at.desc())
    )
    if existing is not None:
        return existing, request
    stop = MediaStop(
        request_id=request.id,
        requester_telegram_id=requester_id,
        chat_id=chat_id,
        confirmation_token=secrets.token_urlsafe(24),
        status=pending_status,
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
    if stop.status not in {"pending_confirmation", "pending_seeding_confirmation"}:
        raise RequestError(f"Stop request is already {stop.status.replace('_', ' ')}.")
    request = db.get(MediaRequest, stop.request_id)
    if request is None or request.automation_id is None:
        raise RequestError("The associated media request no longer exists.")
    try:
        seeding_only = stop.status == "pending_seeding_confirmation"
        stopped = (
            automation.stop_seeding(request.media_type, title=request.title)
            if seeding_only
            else automation.stop_downloads(
                request.automation_id, request.media_type, title=request.title
            )
        )
    except (httpx.HTTPError, KeyError, RuntimeError, ValueError) as exc:
        stop.failure_reason = "The download could not be verified as stopped."
        db.commit()
        raise RequestError(
            "I could not verify that the torrent stopped, so no library files were touched."
        ) from exc
    stop.status = "stopped_seeding" if seeding_only else "stopped"
    stop.stopped_items = len(stopped)
    if not seeding_only:
        request.status = RequestStatus.STOPPED
        request.failure_reason = "Stopped by requester"
    db.commit()
    db.refresh(stop)
    db.refresh(request)
    return stop, request
