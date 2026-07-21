from app.db import SessionLocal, engine
from app.models import Base, MediaType
from app.services.llm import AgentReply, MediaIntent
from app.services.telegram import process_chat_message


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, str | None]] = []

    def send_message(self, chat_id: int, text: str, confirmation_token: str | None = None) -> None:
        self.messages.append((chat_id, text, confirmation_token))


class FakeProvider:
    def __init__(self, reply: AgentReply) -> None:
        self.result = reply
        self.history: list[tuple[str, str]] | None = None

    def reply(self, message: str, history: list[tuple[str, str]]) -> AgentReply:
        self.history = history
        return self.result


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_chat_reply_is_saved_and_sent(monkeypatch) -> None:
    reset_database()
    provider = FakeProvider(AgentReply("Try Arrival."))
    monkeypatch.setattr("app.services.telegram.get_intent_provider", lambda: provider)
    telegram = FakeTelegram()
    with SessionLocal() as db:
        process_chat_message(db, telegram, sender=101, chat_id=555, text="suggest sci-fi")
    with SessionLocal() as db:
        from app.services.chat_history import load_history

        assert load_history(db, 555) == [("user", "suggest sci-fi"), ("assistant", "Try Arrival.")]
    assert telegram.messages == [(555, "Try Arrival.", None)]


def test_chat_request_requires_confirmation(monkeypatch) -> None:
    reset_database()
    provider = FakeProvider(AgentReply("I can add it.", MediaIntent("Arrival", MediaType.MOVIE, 2016)))
    monkeypatch.setattr("app.services.telegram.get_intent_provider", lambda: provider)
    telegram = FakeTelegram()
    with SessionLocal() as db:
        process_chat_message(db, telegram, sender=101, chat_id=555, text="get Arrival")
    assert "Download it?" in telegram.messages[0][1]
    assert telegram.messages[0][2] is not None
