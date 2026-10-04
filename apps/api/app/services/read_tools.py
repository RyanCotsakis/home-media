"""Read-only tools.  Gemini proposes data; this module enforces every boundary."""

from dataclasses import dataclass

import httpx
import sqlglot
from sqlglot import exp
from sqlalchemy import create_engine, text

from app.config import settings


class ReadToolError(ValueError):
    pass


@dataclass(frozen=True)
class ToolResult:
    kind: str
    data: object


def _scoped_sql(sql: str, *, requester_id: int, chat_id: int) -> str:
    """Accept a deliberately small SELECT subset and add an unforgeable owner filter."""
    if ";" in sql or len(sql) > 4000:
        raise ReadToolError("Only one short SELECT statement is allowed.")
    try:
        expression = sqlglot.parse_one(sql, read="postgres")
    except Exception as exc:
        raise ReadToolError("That database query is invalid.") from exc
    if expression.key != "select" or any(expression.find(kind) for kind in (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Copy)):
        raise ReadToolError("Only SELECT queries are allowed.")
    tables = {table.name.lower() for table in expression.find_all(sqlglot.exp.Table)}
    if len(tables) != 1 or not tables <= {"media_requests", "chat_messages"}:
        raise ReadToolError("Queries may use one approved household table only.")
    allowed_functions = {"COUNT", "MIN", "MAX", "AVG", "SUM", "COALESCE", "LOWER", "UPPER"}
    for function in expression.find_all(exp.Func):
        if function.sql_name().upper() not in allowed_functions:
            raise ReadToolError("That SQL function is not allowed.")
    # The owner predicate is injected into the parsed tree, rather than appended
    # to SQL text, so OR precedence cannot widen the selected household rows.
    table = next(iter(tables))
    column, value = ("requester_telegram_id", requester_id) if table == "media_requests" else ("chat_id", chat_id)
    expression = expression.where(exp.EQ(this=exp.column(column), expression=exp.Literal.number(value)), append=True)
    if expression.args.get("limit") is None:
        expression = expression.limit(settings.chat_sql_row_limit)
    return expression.sql(dialect="postgres")


def run_database_query(sql: str, *, requester_id: int, chat_id: int) -> ToolResult:
    if not settings.chat_reader_database_url:
        raise ReadToolError("Database chat access has not been configured.")
    scoped = _scoped_sql(sql, requester_id=requester_id, chat_id=chat_id)
    engine = create_engine(settings.chat_reader_database_url)
    try:
        with engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SET LOCAL statement_timeout = :timeout"), {"timeout": settings.chat_sql_timeout_ms})
            rows = connection.execute(text(scoped)).mappings().fetchmany(settings.chat_sql_row_limit)
            # Avoid leaking confirmation tokens or unbounded transcript content to the provider.
            data = [{key: ("[redacted]" if key == "confirmation_token" else str(value)[:500]) for key, value in row.items()} for row in rows]
            connection.rollback()
    finally:
        engine.dispose()
    return ToolResult("database", data)


def service_status(service: str) -> ToolResult:
    urls = {
        "radarr": (settings.radarr_url, settings.radarr_api_key, "v3", "system/status"),
        "sonarr": (settings.sonarr_url, settings.sonarr_api_key, "v3", "system/status"),
    }
    if service == "qbittorrent":
        if not settings.qbittorrent_username or not settings.qbittorrent_password:
            raise ReadToolError("qBittorrent status is not configured.")
        with httpx.Client(timeout=8) as client:
            login = client.post(f"{settings.qbittorrent_url.rstrip('/')}/api/v2/auth/login", data={"username": settings.qbittorrent_username, "password": settings.qbittorrent_password})
            login.raise_for_status()
            info = client.get(f"{settings.qbittorrent_url.rstrip('/')}/api/v2/transfer/info")
            info.raise_for_status()
            raw = info.json()
            return ToolResult("service", {"service": service, "connection_status": raw.get("connection_status"), "dl_info_speed": raw.get("dl_info_speed"), "free_space_on_disk": raw.get("free_space_on_disk")})
    if service == "jellyfin":
        headers = {"X-Emby-Token": settings.jellyfin_api_key} if settings.jellyfin_api_key else {}
        response = httpx.get(f"{settings.jellyfin_url.rstrip('/')}/Items", headers=headers, params={"Recursive": "true", "Limit": 0}, timeout=8)
        response.raise_for_status()
        raw = response.json()
        return ToolResult("service", {"service": service, "library_items": raw.get("TotalRecordCount"), "status": "ok"})
    if service not in urls:
        raise ReadToolError("That service is not available.")
    base_url, api_key, version, path = urls[service]
    headers = {"X-Api-Key": api_key} if api_key else {}
    response = httpx.get(f"{base_url.rstrip('/')}/api/{version}/{path}" if api_key else f"{base_url.rstrip('/')}/{path}", headers=headers, timeout=8)
    response.raise_for_status()
    raw = response.json() if response.content else {"status": "ok"}
    return ToolResult("service", {"service": service, "version": raw.get("version"), "status": raw.get("status", "ok")})
