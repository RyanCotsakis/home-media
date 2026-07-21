from app.db import SessionLocal, engine
from app.models import Base
from app.services.chat_history import add_message, load_history


def test_history_is_scoped_to_chat_and_keeps_recent_messages(monkeypatch) -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("app.services.chat_history.settings.chat_history_limit", 2)
    with SessionLocal() as db:
        add_message(db, 10, "user", "one")
        add_message(db, 10, "assistant", "two")
        add_message(db, 10, "user", "three")
        add_message(db, 20, "user", "other chat")
        assert load_history(db, 10) == [("assistant", "two"), ("user", "three")]
        assert load_history(db, 20) == [("user", "other chat")]
