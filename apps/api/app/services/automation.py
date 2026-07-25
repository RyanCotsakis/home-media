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
    def submit_request(self, media_id: str, media_type: MediaType, requester_id: int) -> str: ...


class MockAutomationClient:
    """Deterministic stand-in until Radarr/Sonarr adapters are configured."""

    def search_media(self, title: str, media_type: MediaType, year: int | None = None) -> list[MediaCandidate]:
        normalized = " ".join(title.split())
        if not normalized:
            return []
        return [MediaCandidate(f"mock:{media_type}:{normalized.lower()}", normalized.title(), media_type, year)]

    def submit_request(self, media_id: str, media_type: MediaType, requester_id: int) -> str:
        return f"mock-{uuid4()}"


class ArrAutomationClient:
    """Small, server-owned boundary for Radarr, Sonarr and Lidarr APIs."""

    def __init__(self, *, urls: dict[MediaType, str], keys: dict[MediaType, str], root_folders: dict[MediaType, str], quality_profile_names: dict[MediaType, str]):
        self.urls, self.keys, self.root_folders = urls, keys, root_folders
        self.quality_profile_names = quality_profile_names

    def _request(self, media_type: MediaType, method: str, path: str, **kwargs):
        version = "v1" if media_type is MediaType.MUSIC else "v3"
        response = httpx.request(
            method, f"{self.urls[media_type].rstrip('/')}/api/{version}/{path.lstrip('/')}",
            headers={"X-Api-Key": self.keys[media_type]}, timeout=20, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def _quality_profile_id(self, media_type: MediaType) -> int:
        profiles = self._request(media_type, "GET", "qualityprofile")
        if not profiles:
            raise ValueError(f"No quality profile is configured in {media_type.value} automation.")
        if media_type is MediaType.MUSIC:
            # Music requests must only use the profile the household configured
            # specifically for lossless FLAC downloads.
            profile = next((item for item in profiles if "flac" in item.get("name", "").lower()), None)
            if profile is None:
                raise ValueError("Lidarr needs a quality profile with FLAC in its name before album requests can be submitted.")
            return profile["id"]
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
        endpoint = {MediaType.MOVIE: "movie/lookup", MediaType.TV: "series/lookup", MediaType.MUSIC: "album/lookup"}[media_type]
        records = self._request(media_type, "GET", endpoint, params={"term": term})
        candidates: list[MediaCandidate] = []
        for item in records[:10]:
            if media_type is MediaType.MOVIE and item.get("tmdbId"):
                identifier, name = f"tmdb:{item['tmdbId']}", item.get("title")
            elif media_type is MediaType.TV and item.get("tvdbId"):
                identifier, name = f"tvdb:{item['tvdbId']}", item.get("title")
            elif media_type is MediaType.MUSIC and item.get("foreignAlbumId") and item.get("artist", {}).get("foreignArtistId"):
                identifier = f"lidarr:{item['artist']['foreignArtistId']}:{item['foreignAlbumId']}"
                name = f"{item['artist'].get('artistName', 'Unknown artist')} — {item.get('title', 'Unknown album')}"
            else:
                continue
            candidates.append(MediaCandidate(identifier, name or title, media_type, item.get("year")))
        return candidates

    def submit_request(self, media_id: str, media_type: MediaType, requester_id: int) -> str:
        if media_type is MediaType.MOVIE:
            payload = {"tmdbId": int(media_id.removeprefix("tmdb:")), "monitored": True, "qualityProfileId": self._quality_profile_id(media_type), "rootFolderPath": self.root_folders[media_type],
                       "addOptions": {"searchForMovie": True}}
            result = self._request(media_type, "POST", "movie", json=payload)
        elif media_type is MediaType.TV:
            payload = {"tvdbId": int(media_id.removeprefix("tvdb:")), "monitored": True, "qualityProfileId": self._quality_profile_id(media_type), "rootFolderPath": self.root_folders[media_type],
                       "addOptions": {"searchForMissingEpisodes": True}}
            result = self._request(media_type, "POST", "series", json=payload)
        else:
            _, artist_id, album_id = media_id.split(":", 2)
            # Lidarr adds the owning artist, then monitors only the requested album.
            artist = self._request(media_type, "GET", "artist/lookup", params={"term": f"lidarr:{artist_id}"})[0]
            artist.update({"monitored": True, "qualityProfileId": self._quality_profile_id(media_type), "rootFolderPath": self.root_folders[media_type], "addOptions": {"searchForMissingAlbums": False}})
            added_artist = self._request(media_type, "POST", "artist", json=artist)
            albums = self._request(media_type, "GET", "album", params={"artistId": added_artist["id"]})
            album = next((item for item in albums if item.get("foreignAlbumId") == album_id), None)
            if album is None:
                raise httpx.HTTPStatusError("Lidarr did not return the requested album", request=httpx.Request("GET", self.urls[media_type]), response=httpx.Response(404))
            result = self._request(media_type, "PUT", "album/monitor", json={"albumIds": [album["id"]], "monitored": True})
        return f"{media_type.value}:{result.get('id', media_id)}"


def get_automation_client() -> AutomationClient:
    from app.config import settings
    if settings.automation_provider == "arr":
        required = {
            MediaType.MOVIE: settings.radarr_api_key,
            MediaType.TV: settings.sonarr_api_key,
            MediaType.MUSIC: settings.lidarr_api_key,
        }
        if all(required.values()):
            return ArrAutomationClient(
                urls={MediaType.MOVIE: settings.radarr_url, MediaType.TV: settings.sonarr_url, MediaType.MUSIC: settings.lidarr_url},
                keys={kind: key for kind, key in required.items() if key},
                root_folders={MediaType.MOVIE: settings.radarr_root_folder, MediaType.TV: settings.sonarr_root_folder, MediaType.MUSIC: settings.lidarr_root_folder},
                quality_profile_names={
                    MediaType.MOVIE: settings.radarr_quality_profile,
                    MediaType.TV: settings.sonarr_quality_profile,
                    MediaType.MUSIC: "",
                },
            )
    return MockAutomationClient()
