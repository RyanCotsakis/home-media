from app.db import SessionLocal, engine
from app.models import Base, MediaType
from app.services.automation import LibraryItem
from app.services.deletions import confirm_deletion, create_pending_deletion


class FakeLibrary:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, MediaType]] = []

    def find_library_items(self, title: str, media_type: MediaType, year: int | None = None):
        return [LibraryItem("movie:12", "Arrival", MediaType.MOVIE, 2016, "/library/Arrival")]

    def stop_downloads(self, automation_id: str, media_type: MediaType, *, title: str) -> list[str]:
        return []

    def delete_library_item(self, automation_id: str, media_type: MediaType, *, title: str) -> None:
        self.deleted.append((automation_id, media_type))


def test_deletion_requires_owner_confirmation_before_removing_files() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    automation = FakeLibrary()
    with SessionLocal() as db:
        pending = create_pending_deletion(
            db,
            automation,
            requester_id=101,
            chat_id=555,
            title="Arrival",
            media_type=MediaType.MOVIE,
            year=2016,
        )
        assert pending.status == "pending_confirmation"
        assert automation.deleted == []

        deleted = confirm_deletion(
            db,
            automation,
            requester_id=101,
            confirmation_token=pending.confirmation_token,
        )

    assert deleted.status == "deleted"
    assert automation.deleted == [("movie:12", MediaType.MOVIE)]
