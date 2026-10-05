import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MediaDeletion, MediaType
from app.services.automation import AutomationClient
from app.services.requests import RequestError


def create_pending_deletion(
    db: Session,
    automation: AutomationClient,
    *,
    requester_id: int,
    chat_id: int,
    title: str,
    media_type: MediaType,
    year: int | None = None,
) -> MediaDeletion:
    matches = automation.find_library_items(title, media_type, year)
    if not matches:
        raise RequestError(f"I couldn't find {title} in the {media_type.value} library.")
    if len(matches) > 1:
        choices = ", ".join(f"{item.title} ({item.year or 'year unknown'})" for item in matches[:5])
        raise RequestError(f"That matches more than one library item: {choices}. Please include the year.")
    item = matches[0]
    existing = db.scalar(
        select(MediaDeletion)
        .where(
            MediaDeletion.requester_telegram_id == requester_id,
            MediaDeletion.automation_id == item.automation_id,
            MediaDeletion.status == "pending_confirmation",
        )
        .order_by(MediaDeletion.created_at.desc())
    )
    if existing is not None:
        return existing
    deletion = MediaDeletion(
        requester_telegram_id=requester_id,
        chat_id=chat_id,
        media_type=item.media_type,
        automation_id=item.automation_id,
        title=item.title,
        year=item.year,
        path=item.path,
        confirmation_token=secrets.token_urlsafe(24),
    )
    db.add(deletion)
    db.commit()
    db.refresh(deletion)
    return deletion


def confirm_deletion(
    db: Session,
    automation: AutomationClient,
    *,
    requester_id: int,
    confirmation_token: str,
) -> MediaDeletion:
    deletion = db.scalar(select(MediaDeletion).where(MediaDeletion.confirmation_token == confirmation_token))
    if deletion is None or deletion.requester_telegram_id != requester_id:
        raise RequestError("Deletion confirmation is invalid for this user.")
    if deletion.status != "pending_confirmation":
        raise RequestError(f"Deletion is already {deletion.status.replace('_', ' ')}.")
    try:
        automation.delete_library_item(
            deletion.automation_id, deletion.media_type, title=deletion.title
        )
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        deletion.failure_reason = "The media service rejected the deletion."
        db.commit()
        raise RequestError("The media service could not delete that item. Nothing was marked deleted.") from exc
    deletion.status = "deleted"
    db.commit()
    db.refresh(deletion)
    return deletion
