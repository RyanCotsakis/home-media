import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MediaRequest, MediaType, RequestStatus
from app.services.automation import AutomationClient
from app.services.tools import MediaRequestTools


class RequestError(ValueError):
    pass


def create_pending_request(
    db: Session,
    automation: AutomationClient,
    *,
    requester_id: int,
    chat_id: int,
    title: str,
    media_type: MediaType,
    year: int | None = None,
) -> MediaRequest:
    candidates = MediaRequestTools(db, automation).search_media(title, media_type, year)
    if not candidates:
        raise RequestError("No matching media was found")
    candidate = candidates[0]
    active_statuses = (
        RequestStatus.PENDING_CONFIRMATION,
        RequestStatus.SUBMITTED,
        RequestStatus.IMPORTED,
        RequestStatus.NOTIFIED,
    )
    existing = db.scalar(
        select(MediaRequest)
        .where(MediaRequest.media_id == candidate.media_id, MediaRequest.status.in_(active_statuses))
        .order_by(MediaRequest.created_at.desc())
    )
    if existing is not None:
        if existing.status is RequestStatus.PENDING_CONFIRMATION:
            return existing
        raise RequestError(f"{existing.title} is already {existing.status.value.replace('_', ' ')}")
    request = MediaRequest(
        requester_telegram_id=requester_id,
        chat_id=chat_id,
        media_id=candidate.media_id,
        media_type=candidate.media_type,
        title=candidate.title,
        year=candidate.year,
        confirmation_token=secrets.token_urlsafe(24),
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def confirm_request(
    db: Session,
    automation: AutomationClient,
    *,
    requester_id: int,
    confirmation_token: str,
    expected_request_id: str | None = None,
) -> MediaRequest:
    request = db.scalar(select(MediaRequest).where(MediaRequest.confirmation_token == confirmation_token))
    if request is None or request.requester_telegram_id != requester_id:
        raise RequestError("Confirmation token is invalid for this user")
    if expected_request_id is not None and request.id != expected_request_id:
        raise RequestError("Request ID does not match confirmation token")
    if request.status is not RequestStatus.PENDING_CONFIRMATION:
        raise RequestError(f"Request is already {request.status.value}")
    try:
        request.automation_id = MediaRequestTools(db, automation).submit_request(request)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        # Keep the request pending so the owner can retry after correcting
        # Arr configuration; never acknowledge a failed submission as success.
        raise RequestError("The media service could not accept this request. Check its root folder and quality profile, then confirm again.") from exc
    duplicates = db.scalars(
        select(MediaRequest).where(
            MediaRequest.id != request.id,
            MediaRequest.media_id == request.media_id,
            MediaRequest.status == RequestStatus.PENDING_CONFIRMATION,
        )
    ).all()
    for duplicate in duplicates:
        duplicate.status = RequestStatus.FAILED
        duplicate.failure_reason = f"Superseded by request {request.id}"
    request.status = RequestStatus.SUBMITTED
    db.commit()
    db.refresh(request)
    return request


def mark_imported(db: Session, *, automation_id: str) -> MediaRequest:
    request = db.scalar(select(MediaRequest).where(MediaRequest.automation_id == automation_id))
    if request is None:
        raise RequestError("No request is associated with this automation event")
    if request.status is RequestStatus.SUBMITTED:
        request.status = RequestStatus.IMPORTED
        db.commit()
        db.refresh(request)
    return request
