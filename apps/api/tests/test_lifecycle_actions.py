from app.db import SessionLocal, engine
from app.models import Base, MediaRequest, MediaType, RequestStatus
from app.services.chat_history import load_history
from app.services.llm import AgentReply, MediaIntent
from app.services.requests import RequestError
from app.services.telegram import process_chat_message, process_update


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, str | None, str]] = []

    def send_message(
        self,
        chat_id: int,
        text: str,
        confirmation_token: str | None = None,
        *,
        confirmation_action: str = "confirm",
    ) -> None:
        self.messages.append((chat_id, text, confirmation_token, confirmation_action))


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_natural_language_stop_seeding_creates_specific_confirmation(monkeypatch) -> None:
    reset_database()
    with SessionLocal() as db:
        db.add(MediaRequest(
            requester_telegram_id=101,
            chat_id=555,
            media_id="tmdb:329865",
            media_type=MediaType.MOVIE,
            title="Arrival",
            year=2016,
            status=RequestStatus.NOTIFIED,
            confirmation_token="r" * 24,
            automation_id="movie:12",
        ))
        db.commit()

    class Provider:
        def reply(self, message, history):
            return AgentReply(
                "I'll stop seeding it.",
                stop_intent=MediaIntent("Arrival", MediaType.MOVIE, 2016),
                stop_mode="seeding",
            )

    monkeypatch.setattr("app.services.telegram.get_intent_provider", lambda: Provider())
    telegram = FakeTelegram()
    with SessionLocal() as db:
        process_chat_message(db, telegram, sender=101, chat_id=555, text="stop seeding it")

    assert "keeping the imported library files" in telegram.messages[0][1]
    assert telegram.messages[0][2] is not None
    assert telegram.messages[0][3] == "seed"


def test_provider_failure_is_saved_in_chat_history(monkeypatch) -> None:
    reset_database()

    class BrokenProvider:
        def reply(self, message, history):
            raise RequestError("The assistant is temporarily unavailable.")

    monkeypatch.setattr("app.services.telegram.allowed", lambda _: True)
    monkeypatch.setattr("app.services.telegram.get_intent_provider", lambda: BrokenProvider())
    telegram = FakeTelegram()
    with SessionLocal() as db:
        process_update(db, telegram, {
            "message": {"from": {"id": 101}, "chat": {"id": 555}, "text": "delete Arrival"}
        })

    with SessionLocal() as db:
        assert load_history(db, 555) == [
            ("user", "delete Arrival"),
            ("assistant", "The assistant is temporarily unavailable."),
        ]
