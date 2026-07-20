from sqlalchemy.orm import DeclarativeBase
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, Enum, String, Text
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

# class to inherit that makes a class a SQLAlchemy model
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)


class MediaType(StrEnum):
    MOVIE = "movie"
    TV = "tv"


class RequestStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    SUBMITTED = "submitted"
    IMPORTED = "imported"
    NOTIFIED = "notified"
    FAILED = "failed"


class MediaRequest(Base):
    __tablename__ = "media_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Telegram's user and chat identifiers are signed 64-bit values.
    requester_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    media_id: Mapped[str] = mapped_column(String(128))
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, native_enum=False))
    title: Mapped[str] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, native_enum=False), default=RequestStatus.PENDING_CONFIRMATION, index=True
    )
    confirmation_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    automation_id: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
