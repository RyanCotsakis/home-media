"""Local, bounded Telegram conversation memory."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChatMessage


def load_history(db: Session, chat_id: int) -> list[tuple[str, str]]:
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.id.desc()).limit(settings.chat_history_limit)
    ).all()
    return [(message.role, message.content) for message in reversed(messages)]


def add_message(db: Session, chat_id: int, role: str, content: str) -> None:
    db.add(ChatMessage(chat_id=chat_id, role=role, content=content))
    db.flush()
    ids_to_delete = db.scalars(
        select(ChatMessage.id)
        .where(ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.id.desc())
        .offset(settings.chat_history_limit)
    ).all()
    if ids_to_delete:
        db.execute(delete(ChatMessage).where(ChatMessage.id.in_(ids_to_delete)))
    db.commit()
