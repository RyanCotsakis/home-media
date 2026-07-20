import json

from redis import Redis

from app.config import settings

QUEUE_NAME = "home-media:jobs"


def enqueue(job_type: str, payload: dict[str, object]) -> None:
    Redis.from_url(settings.redis_url, decode_responses=True).lpush(
        QUEUE_NAME, json.dumps({"type": job_type, "payload": payload})
    )
