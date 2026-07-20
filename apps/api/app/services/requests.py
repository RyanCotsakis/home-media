import secrets

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
    db: Session, automation: AutomationClient, *, requester_id: int, confirmation_token: str
) -> MediaRequest:
    request = db.scalar(select(MediaRequest).where(MediaRequest.confirmation_token == confirmation_token))
    if request is None or request.requester_telegram_id != requester_id:
        raise RequestError("Confirmation token is invalid for this user")
    if request.status is not RequestStatus.PENDING_CONFIRMATION:
        raise RequestError(f"Request is already {request.status.value}")
    request.automation_id = MediaRequestTools(db, automation).submit_request(request)
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
