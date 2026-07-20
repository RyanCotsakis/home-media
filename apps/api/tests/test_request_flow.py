from fastapi.testclient import TestClient

from app.db import engine
from app.main import app
from app.models import Base


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
