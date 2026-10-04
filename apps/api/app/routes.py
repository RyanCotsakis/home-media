from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import MediaType, User
from app.schemas import (
    ConfirmRequestIn,
    ImportedEventIn,
    MediaRequestOut,
    TelegramUpdateIn,
    UserCreate,
)
from app.services.automation import get_automation_client
from app.services.llm import get_intent_provider
from app.services.queue import enqueue
from app.services.requests import RequestError, confirm_request, create_pending_request, mark_imported

router = APIRouter()


def require_household_user(telegram_user_id: int) -> None:
    if telegram_user_id not in settings.telegram_allowed_user_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Telegram user is not allowed")


def parse_request_text(text: str) -> tuple[MediaType, str, int | None]:
    try:
        intent = get_intent_provider().extract_media_intent(text)
        return intent.media_type, intent.title, intent.year
    except RequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def request_out(request, include_token: bool = False) -> MediaRequestOut:
    return MediaRequestOut(
        id=request.id,
        title=request.title,
        year=request.year,
        media_type=request.media_type,
        status=request.status,
        confirmation_token=request.confirmation_token if include_token else None,
    )


@router.get("/")
def root():
    return {"message": "Home Media API is running"}


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)):
    try:
        db.scalar(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}


@router.post("/v1/telegram/updates", response_model=MediaRequestOut)
def receive_telegram_update(update: TelegramUpdateIn, db: Session = Depends(get_db)):
    require_household_user(update.telegram_user_id)
    media_type, title, year = parse_request_text(update.text)
    try:
        request = create_pending_request(
            db,
            get_automation_client(),
            requester_id=update.telegram_user_id,
            chat_id=update.chat_id,
            title=title,
            media_type=media_type,
            year=year,
        )
    except RequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return request_out(request, include_token=True)


@router.post("/v1/requests/{request_id}/confirm", response_model=MediaRequestOut)
def confirm_media_request(request_id: str, body: ConfirmRequestIn, db: Session = Depends(get_db)):
    require_household_user(body.telegram_user_id)
    try:
        request = confirm_request(
            db,
            get_automation_client(),
            requester_id=body.telegram_user_id,
            confirmation_token=body.confirmation_token,
            expected_request_id=request_id,
        )
    except RequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    enqueue("request_submitted", {"request_id": request.id})
    return request_out(request)


@router.post("/v1/automation/events/imported", response_model=MediaRequestOut)
def imported_event(
    event: ImportedEventIn,
    x_automation_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    if not secrets_equal(x_automation_token, settings.automation_webhook_token):
        raise HTTPException(status_code=401, detail="Invalid automation token")
    try:
        request = mark_imported(db, automation_id=event.automation_id)
    except RequestError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    enqueue("media_imported", {"request_id": request.id})
    return request_out(request)


def automation_id_from_arr_event(event: dict) -> str | None:
    """Translate native Radarr/Sonarr webhooks to our stable ID."""
    event_type = str(event.get("eventType", "")).lower()
    if event_type == "test":
        return None
    if event_type not in {"download", "releaseimport"}:
        raise RequestError("Only completed-download events are accepted")
    mappings = (
        ("movie", "movie"),
        ("series", "tv"),
    )
    for object_name, prefix in mappings:
        item = event.get(object_name)
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            if event_type == "releaseimport":
                continue
            return f"{prefix}:{item['id']}"
    raise RequestError("The automation event has no supported media identifier")


@router.post("/v1/automation/events/arr")
def arr_event(
    event: dict,
    x_automation_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Accept the native JSON body sent by Arr webhook connections."""
    if not secrets_equal(x_automation_token, settings.automation_webhook_token):
        raise HTTPException(status_code=401, detail="Invalid automation token")
    try:
        automation_id = automation_id_from_arr_event(event)
        if automation_id is None:
            return {"status": "ok", "event": "test"}
        try:
            request = mark_imported(db, automation_id=automation_id)
        except RequestError as exc:
            if str(exc) == "No request is associated with this automation event":
                return {"status": "ignored", "automation_id": automation_id}
            raise
    except RequestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    enqueue("media_imported", {"request_id": request.id})
    return {"status": "ok", "request": request_out(request).model_dump(mode="json")}


def secrets_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


# The tutorial CRUD remains available while this is still the shared starter API.
@router.get("/users")
def get_users(db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id)).all()
    return {"users": [{"id": user.id, "email": user.email, "name": user.name} for user in users]}


@router.post("/users")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    new_user = User(email=user.email, name=user.name)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "email": new_user.email, "name": new_user.name}
