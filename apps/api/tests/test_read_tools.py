import pytest

from app.db import SessionLocal, engine
from app.models import Base, MediaRequest, MediaType, RequestStatus
from app.services.read_tools import ReadToolError, _scoped_sql, request_status


def test_scoped_sql_adds_requester_filter_and_limit() -> None:
    query = _scoped_sql("SELECT title, status FROM media_requests ORDER BY created_at DESC", requester_id=42, chat_id=9)
    assert "requester_telegram_id = 42" in query
    assert "LIMIT 25" in query


def test_scoped_sql_scopes_chat_messages_to_chat() -> None:
    query = _scoped_sql("SELECT role FROM chat_messages", requester_id=42, chat_id=9)
    assert "chat_id = 9" in query


@pytest.mark.parametrize("sql", [
    "DELETE FROM media_requests",
    "SELECT * FROM users",
    "SELECT * FROM media_requests; SELECT 1",
    "SELECT * FROM media_requests JOIN chat_messages ON 1 = 1",
])
def test_scoped_sql_rejects_unsafe_or_unapproved_queries(sql: str) -> None:
    with pytest.raises(ReadToolError):
        _scoped_sql(sql, requester_id=42, chat_id=9)


def test_request_status_is_owner_scoped_and_title_filtered() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.add_all([
            MediaRequest(
                requester_telegram_id=42,
                chat_id=9,
                media_id="tvdb:1",
                media_type=MediaType.TV,
                title="Band of Brothers",
                status=RequestStatus.SUBMITTED,
                confirmation_token="a" * 24,
                automation_id="tv:3",
            ),
            MediaRequest(
                requester_telegram_id=99,
                chat_id=10,
                media_id="tmdb:2",
                media_type=MediaType.MOVIE,
                title="Arrival",
                status=RequestStatus.NOTIFIED,
                confirmation_token="b" * 24,
                automation_id="movie:2",
            ),
        ])
        db.commit()
        result = request_status(db, requester_id=42, query="brothers")

    assert len(result.data) == 1
    assert result.data[0]["title"] == "Band of Brothers"
    assert result.data[0]["stage"] == "submitted"
    assert result.data[0]["submitted_to"] == "sonarr"
