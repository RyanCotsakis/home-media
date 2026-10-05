from app.db import SessionLocal, engine
from app.models import Base, MediaRequest, MediaType, RequestStatus
from app.services.stops import confirm_stop, create_pending_stop


class FakeAutomation:
    def __init__(self) -> None:
        self.stopped: list[tuple[str, MediaType, str]] = []

    def stop_downloads(self, automation_id: str, media_type: MediaType, *, title: str):
        self.stopped.append((automation_id, media_type, title))
        return ["Arrival.2016.1080p"]

    def stop_seeding(self, media_type: MediaType, *, title: str):
        self.stopped.append(("seeding", media_type, title))
        return ["Arrival.2016.1080p"]


def test_stop_is_owner_bound_and_updates_request_only_after_confirmation() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    automation = FakeAutomation()
    with SessionLocal() as db:
        request = MediaRequest(
            requester_telegram_id=101,
            chat_id=555,
            media_id="tmdb:329865",
            media_type=MediaType.MOVIE,
            title="Arrival",
            year=2016,
            status=RequestStatus.DOWNLOADING,
            confirmation_token="r" * 24,
            automation_id="movie:12",
        )
        db.add(request)
        db.commit()
        pending, _ = create_pending_stop(
            db, requester_id=101, chat_id=555, title="Arrival"
        )
        assert automation.stopped == []

        stopped, updated = confirm_stop(
            db,
            automation,
            requester_id=101,
            confirmation_token=pending.confirmation_token,
        )

    assert stopped.stopped_items == 1
    assert updated.status is RequestStatus.STOPPED
    assert automation.stopped == [("movie:12", MediaType.MOVIE, "Arrival")]




def test_stop_seeding_keeps_imported_request_state() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    automation = FakeAutomation()
    with SessionLocal() as db:
        request = MediaRequest(
            requester_telegram_id=101,
            chat_id=555,
            media_id="tmdb:329865",
            media_type=MediaType.MOVIE,
            title="Arrival",
            year=2016,
            status=RequestStatus.NOTIFIED,
            confirmation_token="r" * 24,
            automation_id="movie:12",
        )
        db.add(request)
        db.commit()
        pending, _ = create_pending_stop(
            db,
            requester_id=101,
            chat_id=555,
            title="Arrival",
            media_type=MediaType.MOVIE,
            seeding_only=True,
        )
        stopped, updated = confirm_stop(
            db,
            automation,
            requester_id=101,
            confirmation_token=pending.confirmation_token,
        )

    assert stopped.status == "stopped_seeding"
    assert stopped.stopped_items == 1
    assert updated.status is RequestStatus.NOTIFIED
    assert automation.stopped == [("seeding", MediaType.MOVIE, "Arrival")]
