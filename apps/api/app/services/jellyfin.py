"""Small Jellyfin boundary used after an Arr import completes."""

import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def refresh_library() -> bool:
    """Ask Jellyfin to discover newly imported files when it is configured."""
    if not settings.jellyfin_api_key:
        return False
    response = httpx.post(
        f"{settings.jellyfin_url.rstrip('/')}/Library/Refresh",
        headers={"X-Emby-Token": settings.jellyfin_api_key},
        timeout=10,
    )
    if response.status_code in {401, 403}:
        # A stale optional Jellyfin key must not prevent the durable ready
        # notification. Jellyfin also watches its library paths for changes.
        logger.warning("Jellyfin rejected its API key; skipping the explicit library refresh")
        return False
    response.raise_for_status()
    return True
