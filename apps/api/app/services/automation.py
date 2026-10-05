"""Media automation boundary.

Only this module may communicate with Radarr/Sonarr in a production adapter.
The mock makes the request workflow runnable without download infrastructure.
"""

from dataclasses import dataclass
import time
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


@dataclass(frozen=True)
class LibraryItem:
    automation_id: str
    title: str
    media_type: MediaType
    year: int | None = None
    path: str | None = None
    size_on_disk: int | None = None


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
    def find_library_items(self, title: str, media_type: MediaType, year: int | None = None) -> list[LibraryItem]: ...
    def stop_downloads(self, automation_id: str, media_type: MediaType, *, title: str) -> list[str]: ...
    def delete_library_item(self, automation_id: str, media_type: MediaType, *, title: str) -> None: ...


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

    def find_library_items(self, title: str, media_type: MediaType, year: int | None = None) -> list[LibraryItem]:
        return []

    def stop_downloads(self, automation_id: str, media_type: MediaType, *, title: str) -> list[str]:
        return []

    def delete_library_item(self, automation_id: str, media_type: MediaType, *, title: str) -> None:
        raise ValueError("The mock library has no media to delete.")


class ArrAutomationClient:
    """Small, server-owned boundary for Radarr and Sonarr APIs."""

    def __init__(
        self,
        *,
        urls: dict[MediaType, str],
        keys: dict[MediaType, str],
        root_folders: dict[MediaType, str],
        quality_profile_names: dict[MediaType, str],
        qbittorrent_url: str | None = None,
        qbittorrent_username: str | None = None,
        qbittorrent_password: str | None = None,
    ):
        self.urls, self.keys, self.root_folders = urls, keys, root_folders
        self.quality_profile_names = quality_profile_names
        self.qbittorrent_url = qbittorrent_url
        self.qbittorrent_username = qbittorrent_username
        self.qbittorrent_password = qbittorrent_password

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

    def find_library_items(
        self, title: str, media_type: MediaType, year: int | None = None
    ) -> list[LibraryItem]:
        records = self._request(media_type, "GET", "movie" if media_type is MediaType.MOVIE else "series")
        wanted = " ".join(title.casefold().split())
        matches: list[LibraryItem] = []
        for item in records:
            item_title = str(item.get("title", ""))
            normalized = " ".join(item_title.casefold().split())
            item_year = item.get("year")
            if wanted not in normalized or (year is not None and item_year != year):
                continue
            local_id = item.get("id")
            if not isinstance(local_id, int):
                continue
            statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
            size = item.get("sizeOnDisk", statistics.get("sizeOnDisk"))
            matches.append(
                LibraryItem(
                    automation_id=f"{media_type.value}:{local_id}",
                    title=item_title,
                    media_type=media_type,
                    year=item_year,
                    path=item.get("path"),
                    size_on_disk=size if isinstance(size, int) else None,
                )
            )
        exact = [item for item in matches if " ".join(item.title.casefold().split()) == wanted]
        return exact or matches

    @staticmethod
    def _local_id(automation_id: str, media_type: MediaType) -> int:
        prefix, _, raw_id = automation_id.partition(":")
        if prefix != media_type.value or not raw_id.isdigit():
            raise ValueError("The library item identifier is invalid.")
        return int(raw_id)

    def _queue_items(self, media_type: MediaType, local_id: int) -> list[dict]:
        payload = self._request(
            media_type,
            "GET",
            "queue",
            params={"page": 1, "pageSize": 1000, "includeUnknownSeriesItems": "true"},
        )
        records = payload.get("records", []) if isinstance(payload, dict) else []
        id_field = "movieId" if media_type is MediaType.MOVIE else "seriesId"
        object_field = "movie" if media_type is MediaType.MOVIE else "series"
        return [
            item for item in records
            if item.get(id_field) == local_id
            or (
                isinstance(item.get(object_field), dict)
                and item[object_field].get("id") == local_id
            )
        ]

    def _qbittorrent_client(self) -> httpx.Client:
        if not self.qbittorrent_url or not self.qbittorrent_username or not self.qbittorrent_password:
            raise ValueError("qBittorrent verification is not configured.")
        client = httpx.Client(timeout=10)
        response = client.post(
            f"{self.qbittorrent_url.rstrip('/')}/api/v2/auth/login",
            data={"username": self.qbittorrent_username, "password": self.qbittorrent_password},
        )
        response.raise_for_status()
        # qBittorrent versions return either 200 + "Ok." or 204 + a session
        # cookie for a successful login.
        if response.text.strip() and response.text.strip() != "Ok.":
            client.close()
            raise ValueError("qBittorrent rejected its configured credentials.")
        return client

    @staticmethod
    def _title_matches(name: str, title: str) -> bool:
        words = [word for word in title.casefold().split() if len(word) > 2]
        return bool(words) and all(word in name.casefold() for word in words)

    def _matching_qbittorrent_items(
        self, client: httpx.Client, *, hashes: set[str], media_type: MediaType, title: str
    ) -> list[dict]:
        response = client.get(f"{self.qbittorrent_url.rstrip('/')}/api/v2/torrents/info")
        response.raise_for_status()
        category = "movies" if media_type is MediaType.MOVIE else "tv"
        return [
            item for item in response.json()
            if str(item.get("hash", "")).casefold() in hashes
            or (
                str(item.get("category", "")) == category
                and self._title_matches(str(item.get("name", "")), title)
            )
        ]

    def stop_downloads(self, automation_id: str, media_type: MediaType, *, title: str) -> list[str]:
        """Cancel Arr queue entries, remove partial data, and verify qBittorrent is clear."""
        local_id = self._local_id(automation_id, media_type)
        endpoint = "movie" if media_type is MediaType.MOVIE else "series"
        item = self._request(media_type, "GET", f"{endpoint}/{local_id}")
        item["monitored"] = False
        if media_type is MediaType.TV:
            for season in item.get("seasons", []):
                season["monitored"] = False
        self._request(media_type, "PUT", f"{endpoint}/{local_id}", json=item)

        queue_items = self._queue_items(media_type, local_id)
        download_ids = {
            str(entry.get("downloadId", "")).casefold()
            for entry in queue_items
            if entry.get("downloadId")
        }
        stopped_titles = [str(entry.get("title") or title) for entry in queue_items]
        if queue_items:
            self._request(
                media_type,
                "DELETE",
                "queue/bulk",
                params={
                    "removeFromClient": "true",
                    "blocklist": "true",
                    "skipRedownload": "true",
                },
                json={"ids": list(dict.fromkeys(entry["id"] for entry in queue_items))},
            )

        with self._qbittorrent_client() as client:
            remaining = self._matching_qbittorrent_items(
                client, hashes=download_ids, media_type=media_type, title=title
            )
            if remaining:
                hashes = "|".join(str(item["hash"]) for item in remaining)
                stopped_titles.extend(str(item.get("name") or title) for item in remaining)
                stop = client.post(
                    f"{self.qbittorrent_url.rstrip('/')}/api/v2/torrents/stop",
                    data={"hashes": hashes},
                )
                stop.raise_for_status()
                delete = client.post(
                    f"{self.qbittorrent_url.rstrip('/')}/api/v2/torrents/delete",
                    data={"hashes": hashes, "deleteFiles": "true"},
                )
                delete.raise_for_status()
            for _ in range(12):
                if not self._queue_items(media_type, local_id) and not self._matching_qbittorrent_items(
                    client, hashes=download_ids, media_type=media_type, title=title
                ):
                    return list(dict.fromkeys(stopped_titles))
                time.sleep(0.25)
        raise ValueError("The download could not be verified as stopped; library files were not deleted.")

    def delete_library_item(
        self, automation_id: str, media_type: MediaType, *, title: str
    ) -> None:
        # This is deliberately enforced inside the destructive boundary so no
        # caller can delete library files while a matching torrent is active.
        self.stop_downloads(automation_id, media_type, title=title)
        local_id = self._local_id(automation_id, media_type)
        endpoint = "movie" if media_type is MediaType.MOVIE else "series"
        exclusion_flag = "addImportExclusion" if media_type is MediaType.MOVIE else "addImportListExclusion"
        self._request(
            media_type,
            "DELETE",
            f"{endpoint}/{local_id}",
            params={"deleteFiles": "true", exclusion_flag: "false"},
        )


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
            qbittorrent_url=settings.qbittorrent_url,
            qbittorrent_username=settings.qbittorrent_username,
            qbittorrent_password=settings.qbittorrent_password,
        )
    if settings.automation_provider != "mock":
        raise ValueError(f"Unsupported automation provider: {settings.automation_provider}")
    return MockAutomationClient()
