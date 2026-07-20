from pydantic import BaseModel, EmailStr, Field

from app.models import MediaType, RequestStatus

class UserCreate(BaseModel):
    email: EmailStr
    name: str


class TelegramUpdateIn(BaseModel):
    telegram_user_id: int
    chat_id: int
    text: str = Field(min_length=1, max_length=500)


class ConfirmRequestIn(BaseModel):
    telegram_user_id: int
    confirmation_token: str = Field(min_length=16, max_length=64)


class ImportedEventIn(BaseModel):
    automation_id: str = Field(min_length=1, max_length=128)


class MediaRequestOut(BaseModel):
    id: str
    title: str
    year: int | None
    media_type: MediaType
    status: RequestStatus
    confirmation_token: str | None = None
