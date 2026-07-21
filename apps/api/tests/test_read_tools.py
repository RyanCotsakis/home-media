import pytest

from app.services.read_tools import ReadToolError, _scoped_sql


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
