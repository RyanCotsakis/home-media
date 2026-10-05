from typing import Any

import pytest

from app.models import MediaType
from app.services.automation import ArrAutomationClient


class RecordingArrClient(ArrAutomationClient):
    def __init__(self, responses: dict[tuple[MediaType, str, str], Any]):
        super().__init__(
            urls={kind: f"http://{kind.value}" for kind in MediaType},
            keys={kind: "key" for kind in MediaType},
            root_folders={
                MediaType.MOVIE: "/data/library/movies",
                MediaType.TV: "/data/library/tv",
            },
            quality_profile_names={
                MediaType.MOVIE: "HD-720p",
                MediaType.TV: "HD-720p",
            },
        )
        self.responses = responses
        self.calls: list[tuple[MediaType, str, str, dict]] = []
        self.stop_calls: list[tuple[str, MediaType, str]] = []

    def _request(self, media_type: MediaType, method: str, path: str, **kwargs):
        self.calls.append((media_type, method, path, kwargs))
        return self.responses[(media_type, method, path)]

    def stop_downloads(self, automation_id: str, media_type: MediaType, *, title: str) -> list[str]:
        self.stop_calls.append((automation_id, media_type, title))
        return []


def test_tv_submission_uses_canonical_lookup_record_and_required_title() -> None:
    client = RecordingArrClient({
        (MediaType.TV, "GET", "series"): [],
        (MediaType.TV, "GET", "series/lookup"): [{
            "id": 0,
            "tvdbId": 371980,
            "title": "Severance",
            "titleSlug": "severance",
            "images": [{"coverType": "poster", "remoteUrl": "https://example.test/poster.jpg"}],
            "seasons": [{"seasonNumber": 1, "monitored": False}],
        }],
        (MediaType.TV, "GET", "qualityprofile"): [{"id": 4, "name": "HD-720p"}],
        (MediaType.TV, "POST", "series"): {"id": 27},
    })

    automation_id = client.submit_request(
        "tvdb:371980", MediaType.TV, 101, title="Severance", year=2022
    )

    assert automation_id == "tv:27"
    lookup = next(call for call in client.calls if call[2] == "series/lookup")
    assert lookup[3]["params"] == {"term": "tvdb:371980"}
    submission = next(call for call in client.calls if call[1:3] == ("POST", "series"))
    payload = submission[3]["json"]
    assert payload["title"] == "Severance"
    assert payload["titleSlug"] == "severance"
    assert payload["tvdbId"] == 371980
    assert payload["qualityProfileId"] == 4
    assert payload["rootFolderPath"] == "/data/library/tv"
    assert payload["addOptions"]["monitor"] == "all"
    assert payload["addOptions"]["searchForMissingEpisodes"] is True
    assert "id" not in payload


def test_existing_series_is_idempotent() -> None:
    client = RecordingArrClient({
        (MediaType.TV, "GET", "series"): [{"id": 9, "tvdbId": 371980}],
        (MediaType.TV, "POST", "command"): {"id": 10},
    })

    automation_id = client.submit_request(
        "tvdb:371980", MediaType.TV, 101, title="Severance", year=2022
    )

    assert automation_id == "tv:9"
    command = next(call for call in client.calls if call[2] == "command")
    assert command[3]["json"] == {"name": "SeriesSearch", "seriesId": 9}




def test_library_lookup_and_delete_use_arr_file_removal() -> None:
    client = RecordingArrClient({
        (MediaType.MOVIE, "GET", "movie"): [{
            "id": 12,
            "title": "Arrival",
            "year": 2016,
            "path": "/data/library/movies/Arrival (2016)",
            "sizeOnDisk": 900_000_000,
        }],
        (MediaType.MOVIE, "DELETE", "movie/12"): {},
    })

    matches = client.find_library_items("Arrival", MediaType.MOVIE, 2016)
    assert matches[0].automation_id == "movie:12"
    assert matches[0].size_on_disk == 900_000_000

    client.delete_library_item(matches[0].automation_id, MediaType.MOVIE, title="Arrival")
    assert client.stop_calls == [("movie:12", MediaType.MOVIE, "Arrival")]
    deletion = next(call for call in client.calls if call[1] == "DELETE")
    assert deletion[3]["params"] == {"deleteFiles": "true", "addImportExclusion": "false"}


def test_library_files_are_not_deleted_when_stop_verification_fails() -> None:
    class UnsafeClient(RecordingArrClient):
        def stop_downloads(self, automation_id: str, media_type: MediaType, *, title: str):
            raise ValueError("torrent is still active")

    client = UnsafeClient({(MediaType.MOVIE, "DELETE", "movie/12"): {}})

    with pytest.raises(ValueError, match="still active"):
        client.delete_library_item("movie:12", MediaType.MOVIE, title="Arrival")

    assert not any(call[1] == "DELETE" for call in client.calls)


def test_stop_uses_bulk_queue_removal_for_multi_episode_pack() -> None:
    class Response:
        text = "Ok."

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return []

    class Qbit:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return Response()

        def post(self, *args, **kwargs):
            return Response()

    class StopClient(RecordingArrClient):
        def __init__(self):
            super().__init__({
                (MediaType.TV, "GET", "series/3"): {
                    "id": 3, "title": "Band of Brothers", "monitored": True,
                    "seasons": [{"seasonNumber": 1, "monitored": True}],
                },
                (MediaType.TV, "PUT", "series/3"): {},
                (MediaType.TV, "DELETE", "queue/bulk"): {},
            })
            self.qbittorrent_url = "http://qbittorrent:8080"
            self.queue_checks = 0

        def _queue_items(self, media_type: MediaType, local_id: int):
            self.queue_checks += 1
            if self.queue_checks == 1:
                return [
                    {"id": 10, "downloadId": "HASH", "title": "Episode 1"},
                    {"id": 11, "downloadId": "HASH", "title": "Episode 2"},
                ]
            return []

        def _qbittorrent_client(self):
            return Qbit()

    client = StopClient()
    stopped = ArrAutomationClient.stop_downloads(
        client, "tv:3", MediaType.TV, title="Band of Brothers"
    )

    bulk = next(call for call in client.calls if call[1:3] == ("DELETE", "queue/bulk"))
    assert bulk[3]["json"] == {"ids": [10, 11]}
    assert bulk[3]["params"]["removeFromClient"] == "true"
    assert stopped == ["Episode 1", "Episode 2"]




def test_stop_seeding_removes_completed_legacy_category_torrent_only() -> None:
    class Response:
        def __init__(self, data=None):
            self.data = data
            self.text = "Ok." if data is None else ""

        def raise_for_status(self) -> None:
            pass

        def json(self):
            return self.data

    class Qbit:
        def __init__(self):
            self.deleted = False
            self.posts: list[tuple[str, dict]] = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, data):
            self.posts.append((url, data))
            if url.endswith("/torrents/delete"):
                self.deleted = True
            return Response()

        def get(self, *args, **kwargs):
            torrents = [] if self.deleted else [{
                "hash": "ABC",
                "name": "Arrival.2016.720p",
                "category": "radarr",
                "progress": 1,
            }]
            return Response(torrents)

    class SeedClient(RecordingArrClient):
        def __init__(self):
            super().__init__({})
            self.qbittorrent_url = "http://qbittorrent:8080"
            self.qbit = Qbit()

        def _qbittorrent_client(self):
            return self.qbit

    client = SeedClient()
    stopped = client.stop_seeding(MediaType.MOVIE, title="Arrival")

    assert stopped == ["Arrival.2016.720p"]
    deletion = next(call for call in client.qbit.posts if call[0].endswith("/torrents/delete"))
    assert deletion[1] == {"hashes": "ABC", "deleteFiles": "true"}
    assert client.calls == []
