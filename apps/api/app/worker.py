"""Minimal Redis worker boundary for retry-safe delivery work.

The production Telegram poller and Radarr/Sonarr webhook adapters enqueue jobs;
this worker owns delivery state so API requests never block on network calls.
"""

import json
import logging
import time
from datetime import UTC, datetime

from redis import Redis
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from app.config import settings
from app.db import SessionLocal
from app.models import MediaRequest, RequestStatus
from app.services.jellyfin import refresh_library
from app.services.queue import QUEUE_NAME
from app.services.telegram import TelegramClient, process_update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# HTTPX includes complete request URLs in INFO logs. Telegram bot tokens are
# embedded in those URLs, so never emit them in routine container logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def handle_job(job: dict[str, object], telegram: TelegramClient | None = None) -> None:
    payload = job.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("job payload must be an object")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str):
        raise ValueError("job must contain request_id")
    if job.get("type") != "media_imported":
        logger.info("processed job %s for request %s", job.get("type"), request_id)
        return
    with SessionLocal() as db:
        request = db.get(MediaRequest, request_id)
        if request is None or request.ready_notified_at is not None:
            return
        message = f"{request.title} is ready to watch in Jellyfin."
        refresh_library()
        if telegram:
            telegram.send_message(request.chat_id, message)
        else:
            logger.info("mock notification for chat %s: %s", request.chat_id, message)
        # Set this only after delivery succeeds, making a retried job idempotent.
        request.ready_notified_at = datetime.now(UTC)
        request.status = RequestStatus.NOTIFIED
        db.commit()
        logger.info("marked request %s ready for notification", request_id)


def main() -> None:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    telegram = TelegramClient(settings.telegram_bot_token) if settings.telegram_bot_token else None
    next_offset: int | None = None
    while True:
        if telegram:
            try:
                for update in telegram.get_updates(next_offset):
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        next_offset = update_id + 1
                        # Prevent replayed Telegram updates after a worker restart.
                        if not client.set(f"home-media:telegram-update:{update_id}", "1", nx=True, ex=86_400):
                            continue
                    with SessionLocal() as db:
                        process_update(db, telegram, update)
            except Exception:
                logger.exception("Telegram polling failed; retrying")
        try:
            item = client.brpop(QUEUE_NAME, timeout=5)
        except (RedisTimeoutError, RedisError):
            # A timeout is normal for an idle queue. Keep the Telegram poller
            # alive rather than letting the worker exit and restart.
            logger.debug("Redis queue read timed out; continuing")
            continue
        if item is None:
            continue
        _, raw_job = item
        try:
            handle_job(json.loads(raw_job), telegram)
        except Exception:
            logger.exception("job failed; re-queueing for retry")
            client.lpush(QUEUE_NAME, raw_job)
            time.sleep(1)


if __name__ == "__main__":
    main()
