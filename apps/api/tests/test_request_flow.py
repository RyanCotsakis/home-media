from fastapi.testclient import TestClient

from app.db import engine
from app.main import app
from app.models import Base
from app.routes import automation_id_from_arr_event


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_allowlisted_user_can_create_and_confirm_request(monkeypatch) -> None:
    reset_database()
    monkeypatch.setattr("app.routes.enqueue", lambda *_: None)
    with TestClient(app) as client:
        created = client.post(
            "/v1/telegram/updates",
            json={"telegram_user_id": 101, "chat_id": 555, "text": "/movie Arrival"},
        )
        assert created.status_code == 200
        body = created.json()
        assert body["status"] == "pending_confirmation"
        confirmed = client.post(
            f"/v1/requests/{body['id']}/confirm",
            json={"telegram_user_id": 101, "confirmation_token": body["confirmation_token"]},
        )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "submitted"


def test_non_household_user_is_rejected() -> None:
    reset_database()
    with TestClient(app) as client:
        response = client.post(
            "/v1/telegram/updates",
            json={"telegram_user_id": 999, "chat_id": 555, "text": "/tv Severance"},
        )
    assert response.status_code == 403


def test_duplicate_pending_request_reuses_confirmation() -> None:
    reset_database()
    with TestClient(app) as client:
        first = client.post(
            "/v1/telegram/updates",
            json={"telegram_user_id": 101, "chat_id": 555, "text": "/tv Severance"},
        ).json()
        second = client.post(
            "/v1/telegram/updates",
            json={"telegram_user_id": 101, "chat_id": 555, "text": "/tv Severance"},
        ).json()

    assert second["id"] == first["id"]
    assert second["confirmation_token"] == first["confirmation_token"]


def test_mismatched_request_id_does_not_submit(monkeypatch) -> None:
    reset_database()
    monkeypatch.setattr("app.routes.enqueue", lambda *_: None)
    with TestClient(app) as client:
        created = client.post(
            "/v1/telegram/updates",
            json={"telegram_user_id": 101, "chat_id": 555, "text": "/movie Arrival"},
        ).json()
        mismatch = client.post(
            "/v1/requests/not-the-request/confirm",
            json={"telegram_user_id": 101, "confirmation_token": created["confirmation_token"]},
        )
        confirmed = client.post(
            f"/v1/requests/{created['id']}/confirm",
            json={"telegram_user_id": 101, "confirmation_token": created["confirmation_token"]},
        )

    assert mismatch.status_code == 409
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "submitted"


def test_import_event_is_idempotent(monkeypatch) -> None:
    reset_database()
    monkeypatch.setattr("app.routes.enqueue", lambda *_: None)
    with TestClient(app) as client:
        created = client.post(
            "/v1/telegram/updates",
            json={"telegram_user_id": 101, "chat_id": 555, "text": "/movie Arrival"},
        ).json()
        client.post(
            f"/v1/requests/{created['id']}/confirm",
            json={"telegram_user_id": 101, "confirmation_token": created["confirmation_token"]},
        )
        # The mock adapter ID is not exposed by the user-facing API; seed an event
        # against the database path through the confirmed request response in live adapters.
        from app.db import SessionLocal
        from app.models import MediaRequest
        with SessionLocal() as db:
            automation_id = db.get(MediaRequest, created["id"]).automation_id
        headers = {"X-Automation-Token": "test-webhook-token"}
        first = client.post("/v1/automation/events/imported", json={"automation_id": automation_id}, headers=headers)
        second = client.post("/v1/automation/events/imported", json={"automation_id": automation_id}, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "imported"


def test_native_arr_download_webhook_marks_tv_request_imported(monkeypatch) -> None:
    reset_database()
    monkeypatch.setattr("app.routes.enqueue", lambda *_: None)
    with TestClient(app) as client:
        created = client.post(
            "/v1/telegram/updates",
            json={"telegram_user_id": 101, "chat_id": 555, "text": "/tv Severance"},
        ).json()
        client.post(
            f"/v1/requests/{created['id']}/confirm",
            json={"telegram_user_id": 101, "confirmation_token": created["confirmation_token"]},
        )
        from app.db import SessionLocal
        from app.models import MediaRequest

        with SessionLocal() as db:
            request = db.get(MediaRequest, created["id"])
            request.automation_id = "tv:27"
            db.commit()
        response = client.post(
            "/v1/automation/events/arr",
            json={"eventType": "Download", "series": {"id": 27, "title": "Severance"}},
            headers={"X-Automation-Token": "test-webhook-token"},
        )

    assert response.status_code == 200
    assert response.json()["request"]["status"] == "imported"


def test_native_arr_test_webhook_is_accepted() -> None:
    reset_database()
    with TestClient(app) as client:
        response = client.post(
            "/v1/automation/events/arr",
            json={"eventType": "Test"},
            headers={"X-Automation-Token": "test-webhook-token"},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "event": "test"}


def test_untracked_arr_download_webhook_is_ignored(monkeypatch) -> None:
    reset_database()
    monkeypatch.setattr("app.routes.enqueue", lambda *_: None)
    with TestClient(app) as client:
        response = client.post(
            "/v1/automation/events/arr",
            json={"eventType": "Download", "movie": {"id": 999}},
            headers={"X-Automation-Token": "test-webhook-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "automation_id": "movie:999"}


def test_arr_webhook_id_mapping() -> None:
    assert automation_id_from_arr_event({"eventType": "Download", "movie": {"id": 4}}) == "movie:4"
    assert automation_id_from_arr_event({"eventType": "Download", "series": {"id": 5}}) == "tv:5"
