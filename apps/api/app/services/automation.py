"""Media automation boundary.

Only this module may communicate with Radarr/Sonarr in a production adapter.
The mock makes the request workflow runnable without download infrastructure.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import httpx

from app.models import MediaType


@dataclass(frozen=True)
class MediaCandidate:
    media_id: str
    title: str
    media_type: MediaType
    year: int | None = None


class AutomationClient(Protocol):
    def search_media(self, title: str, media_type: MediaType, year: int | None = None) -> list[MediaCandidate]: ...
    def submit_request(
        self,
        media_id: str,
        media_type: MediaType,
        requester_id: int,
        *,
        title: str,
        year: int | None = None,
    ) -> str: ...


class MockAutomationClient:
    """Deterministic stand-in until Radarr/Sonarr adapters are configured."""

    def search_media(self, title: str, media_type: MediaType, year: int | None = None) -> list[MediaCandidate]:
        normalized = " ".join(title.split())
        if not normalized:
            return []
        return [MediaCandidate(f"mock:{media_type}:{normalized.lower()}", normalized.title(), media_type, year)]

    def submit_request(
        self,
        media_id: str,
        media_type: MediaType,
        requester_id: int,
        *,
        title: str,
        year: int | None = None,
    ) -> str:
        return f"mock-{uuid4()}"


class ArrAutomationClient:
    """Small, server-owned boundary for Radarr and Sonarr APIs."""

    def __init__(self, *, urls: dict[MediaType, str], keys: dict[MediaType, str], root_folders: dict[MediaType, str], quality_profile_names: dict[MediaType, str]):
        self.urls, self.keys, self.root_folders = urls, keys, root_folders
        self.quality_profile_names = quality_profile_names

    def _request(self, media_type: MediaType, method: str, path: str, **kwargs):
        if not self.keys.get(media_type):
            raise ValueError(f"The {media_type.value} automation API key is not configured.")
        response = httpx.request(
            method, f"{self.urls[media_type].rstrip('/')}/api/v3/{path.lstrip('/')}",
            headers={"X-Api-Key": self.keys[media_type]}, timeout=20, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def _quality_profile_id(self, media_type: MediaType) -> int:
        profiles = self._request(media_type, "GET", "qualityprofile")
        if not profiles:
            raise ValueError(f"No quality profile is configured in {media_type.value} automation.")
        profile_name = self.quality_profile_names[media_type]
        profile = next((item for item in profiles if item.get("name") == profile_name), None)
        if profile is None:
            raise ValueError(
                f"{media_type.value.title()} needs a quality profile named {profile_name!r}. "
                "Create it in the Arr service or set the corresponding quality-profile setting."
            )
        return profile["id"]

    def search_media(self, title: str, media_type: MediaType, year: int | None = None) -> list[MediaCandidate]:
        term = f"{title} {year}" if year else title
        endpoint = {MediaType.MOVIE: "movie/lookup", MediaType.TV: "series/lookup"}[media_type]
        records = self._request(media_type, "GET", endpoint, params={"term": term})
        candidates: list[MediaCandidate] = []
        for item in records[:10]:
            if media_type is MediaType.MOVIE and item.get("tmdbId"):
                identifier, name = f"tmdb:{item['tmdbId']}", item.get("title")
            elif media_type is MediaType.TV and item.get("tvdbId"):
                identifier, name = f"tvdb:{item['tvdbId']}", item.get("title")
            else:
                continue
            candidates.append(MediaCandidate(identifier, name or title, media_type, item.get("year")))
        return candidates

    def _lookup_by_id(self, media_type: MediaType, media_id: str) -> dict:
        """Return the canonical Arr lookup record used to create an item.

        Sonarr and Radarr require more than an external ID when adding media.
        Re-fetching the record at confirmation time supplies version-compatible
        fields such as title/titleSlug, images, seasons, and release metadata.
        """
        endpoint = "movie/lookup" if media_type is MediaType.MOVIE else "series/lookup"
        records = self._request(media_type, "GET", endpoint, params={"term": media_id})
        external_key = "tmdbId" if media_type is MediaType.MOVIE else "tvdbId"
        expected_id = int(media_id.split(":", 1)[1])
        record = next((item for item in records if item.get(external_key) == expected_id), None)
        if record is None:
            raise ValueError(f"{media_type.value.title()} lookup no longer returns {media_id}.")
        # Lookup resources occasionally carry id=0. Never send it as the local
        # database identity when creating an item.
        record = dict(record)
        record.pop("id", None)
        return record

    def _existing_item(self, media_type: MediaType, external_key: str, external_id: int) -> dict | None:
        records = self._request(media_type, "GET", "movie" if media_type is MediaType.MOVIE else "series")
        return next((item for item in records if item.get(external_key) == external_id), None)

    def submit_request(
        self,
        media_id: str,
        media_type: MediaType,
        requester_id: int,
        *,
        title: str,
        year: int | None = None,
    ) -> str:
        if media_type is MediaType.MOVIE:
            tmdb_id = int(media_id.removeprefix("tmdb:"))
            existing = self._existing_item(media_type, "tmdbId", tmdb_id)
            if existing is not None:
                self._request(
                    media_type,
                    "POST",
                    "command",
                    json={"name": "MoviesSearch", "movieIds": [existing["id"]]},
                )
                return f"movie:{existing['id']}"
            payload = self._lookup_by_id(media_type, media_id)
            payload.update({
                "tmdbId": tmdb_id,
                "title": payload.get("title") or title,
                "monitored": True,
                "qualityProfileId": self._quality_profile_id(media_type),
                "rootFolderPath": self.root_folders[media_type],
                "addOptions": {"searchForMovie": True},
            })
            result = self._request(media_type, "POST", "movie", json=payload)
        elif media_type is MediaType.TV:
            tvdb_id = int(media_id.removeprefix("tvdb:"))
            existing = self._existing_item(media_type, "tvdbId", tvdb_id)
            if existing is not None:
                self._request(
                    media_type,
                    "POST",
                    "command",
                    json={"name": "SeriesSearch", "seriesId": existing["id"]},
                )
                return f"tv:{existing['id']}"
            payload = self._lookup_by_id(media_type, media_id)
            payload.update({
                "tvdbId": tvdb_id,
                "title": payload.get("title") or title,
                "monitored": True,
                "seasonFolder": True,
                "qualityProfileId": self._quality_profile_id(media_type),
                "rootFolderPath": self.root_folders[media_type],
                "addOptions": {
                    "monitor": "all",
                    "searchForMissingEpisodes": True,
                    "searchForCutoffUnmetEpisodes": False,
                },
            })
            result = self._request(media_type, "POST", "series", json=payload)
        return f"{media_type.value}:{result.get('id', media_id)}"


def get_automation_client() -> AutomationClient:
    from app.config import settings
    if settings.automation_provider == "arr":
        keys = {
            MediaType.MOVIE: settings.radarr_api_key,
            MediaType.TV: settings.sonarr_api_key,
        }
        return ArrAutomationClient(
            urls={MediaType.MOVIE: settings.radarr_url, MediaType.TV: settings.sonarr_url},
            keys={kind: key or "" for kind, key in keys.items()},
            root_folders={MediaType.MOVIE: settings.radarr_root_folder, MediaType.TV: settings.sonarr_root_folder},
            quality_profile_names={
                MediaType.MOVIE: settings.radarr_quality_profile,
                MediaType.TV: settings.sonarr_quality_profile,
            },
        )
    if settings.automation_provider != "mock":
        raise ValueError(f"Unsupported automation provider: {settings.automation_provider}")
    return MockAutomationClient()
