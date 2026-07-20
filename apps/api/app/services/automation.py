"""Media automation boundary.

Only this module may communicate with Radarr/Sonarr in a production adapter.
The mock makes the request workflow runnable without download infrastructure.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

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


def get_automation_client() -> AutomationClient:
    # Future adapters are selected here; this keeps routes and LLM tooling isolated.
    return MockAutomationClient()
