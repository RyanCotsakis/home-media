"""The complete set of actions available to an LLM-facing orchestration layer."""

from sqlalchemy.orm import Session

from app.models import MediaRequest, MediaType
from app.services.automation import AutomationClient, MediaCandidate


class MediaRequestTools:
    def __init__(self, db: Session, automation: AutomationClient):
        self.db = db
        self.automation = automation

    def search_media(self, title: str, media_type: MediaType, year: int | None = None) -> list[MediaCandidate]:
        return self.automation.search_media(title, media_type, year)

    def submit_request(self, request: MediaRequest) -> str:
        return self.automation.submit_request(request.media_id, request.media_type, request.requester_telegram_id)

    def get_request_status(self, request_id: str) -> MediaRequest | None:
        return self.db.get(MediaRequest, request_id)
