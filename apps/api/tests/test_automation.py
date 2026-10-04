from typing import Any

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

    def _request(self, media_type: MediaType, method: str, path: str, **kwargs):
        self.calls.append((media_type, method, path, kwargs))
        return self.responses[(media_type, method, path)]


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
