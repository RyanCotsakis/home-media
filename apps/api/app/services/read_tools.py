"""Read-only tools.  Gemini proposes data; this module enforces every boundary."""

from dataclasses import dataclass
import re

import httpx
import sqlglot
from sqlglot import exp
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MediaRequest


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


def request_status(db: Session, *, requester_id: int, query: str | None = None) -> ToolResult:
    """Return bounded, owner-scoped request state without executing LLM-authored SQL."""
    statement = select(MediaRequest).where(MediaRequest.requester_telegram_id == requester_id)
    if query and query.strip():
        statement = statement.where(MediaRequest.title.ilike(f"%{query.strip()}%"))
    statement = statement.order_by(MediaRequest.created_at.desc()).limit(10)
    requests = db.scalars(statement).all()
    data = [
        {
            "title": item.title,
            "year": item.year,
            "media_type": item.media_type.value,
            "stage": item.status.value,
            "submitted_to": (
                {"movie": "radarr", "tv": "sonarr"}.get(item.automation_id.split(":", 1)[0])
                if item.automation_id else None
            ),
            "failure_reason": item.failure_reason,
            "updated_at": item.updated_at.isoformat(),
        }
        for item in requests
    ]
    return ToolResult("requests", data)


def active_downloads() -> ToolResult:
    """Return current movie/TV torrents with human-meaningful progress fields."""
    if not settings.qbittorrent_username or not settings.qbittorrent_password:
        raise ReadToolError("qBittorrent download status is not configured.")
    with httpx.Client(timeout=8) as client:
        login = client.post(
            f"{settings.qbittorrent_url.rstrip('/')}/api/v2/auth/login",
            data={"username": settings.qbittorrent_username, "password": settings.qbittorrent_password},
        )
        login.raise_for_status()
        response = client.get(
            f"{settings.qbittorrent_url.rstrip('/')}/api/v2/torrents/info",
            params={"filter": "all", "sort": "added_on", "reverse": "true"},
        )
        response.raise_for_status()
    rows = []
    for torrent in response.json():
        progress = float(torrent.get("progress") or 0)
        category = str(torrent.get("category") or "")
        if progress >= 1 or category not in {"movies", "radarr", "tv", "sonarr"}:
            continue
        eta = torrent.get("eta")
        rows.append(
            {
                "name": str(torrent.get("name") or "")[:300],
                "category": category,
                "state": torrent.get("state"),
                "percent_complete": round(progress * 100, 1),
                "download_speed_bytes_per_second": torrent.get("dlspeed"),
                "eta_seconds": eta if isinstance(eta, int) and eta < 8_640_000 else None,
                "bytes_remaining": torrent.get("amount_left"),
                "total_size_bytes": torrent.get("size"),
                "resolution": (
                    match.group(1)
                    if (match := re.search(r"\b(2160p|1080p|720p|576p|480p)\b", str(torrent.get("name") or ""), re.I))
                    else None
                ),
            }
        )
    return ToolResult("downloads", rows[:25])


def request_status_with_downloads(
    db: Session, *, requester_id: int, query: str | None = None
) -> ToolResult:
    """Combine durable request stages with live downloader state when reachable."""
    requests = request_status(db, requester_id=requester_id, query=query).data
    try:
        downloads = active_downloads().data
        downloads_available = True
    except (ReadToolError, httpx.HTTPError):
        downloads = []
        downloads_available = False
    if query and isinstance(downloads, list):
        words = [word for word in query.casefold().split() if len(word) > 2]
        downloads = [
            item for item in downloads
            if isinstance(item, dict) and all(word in str(item.get("name", "")).casefold() for word in words)
        ]
    return ToolResult(
        "requests",
        {
            "requests": requests,
            "active_downloads": downloads,
            "download_status_available": downloads_available,
        },
    )


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
