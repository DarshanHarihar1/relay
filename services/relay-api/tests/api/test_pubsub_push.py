from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.domain.ingestion import GoogleConnection
from app.main import app


def _envelope(*, email_address: str = "mailbox@example.test", history_id: int = 900) -> dict:
    encoded = base64.b64encode(
        json.dumps({"emailAddress": email_address, "historyId": str(history_id)}).encode()
    ).decode()
    return {
        "message": {
            "data": encoded,
            "messageId": "pubsub-1",
            "publishTime": "2026-08-23T08:00:00Z",
        },
        "subscription": "projects/relay/subscriptions/gmail-events-api",
    }


class FakeVerifier:
    def __init__(self, claims: dict):
        self._claims = claims

    async def verify(self, token: str, audience: str) -> dict:
        return self._claims


class FakeConnections:
    def __init__(self, connections: list[GoogleConnection]):
        self._connections = connections

    async def get_active_connections_by_gmail_email(self, email_address: str) -> list[GoogleConnection]:
        return [
            connection
            for connection in self._connections
            if connection.gmail_email_address == email_address
        ]


class FakeQueue:
    def __init__(self) -> None:
        self.commands = []

    async def enqueue(self, command) -> None:
        self.commands.append(command)


def _connection() -> GoogleConnection:
    return GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
        gmail_email_address="mailbox@example.test",
        gmail_label_id="Label_123",
        encrypted_refresh_token="encrypted",
        connected_at=datetime.now(timezone.utc),
    )


def test_push_rejects_wrong_oidc_audience() -> None:
    from app.routes.pubsub import GmailPubSubHandler, get_gmail_pubsub_handler

    handler = GmailPubSubHandler(
        connections=FakeConnections([_connection()]),
        queue=FakeQueue(),
        verifier=FakeVerifier(
            {
                "iss": "https://accounts.google.com",
                "aud": "https://wrong.example/v1/events/gmail",
                "email": "relay-push@example.iam.gserviceaccount.com",
                "email_verified": True,
            }
        ),
        audience="https://relay.example/v1/events/gmail",
        service_account_email="relay-push@example.iam.gserviceaccount.com",
    )
    app.dependency_overrides[get_gmail_pubsub_handler] = lambda: handler
    try:
        response = TestClient(app).post(
            "/v1/events/gmail",
            headers={"Authorization": "Bearer wrong-aud-token"},
            json=_envelope(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_push_queues_only_the_single_connection_for_mailbox() -> None:
    from app.routes.pubsub import GmailPubSubHandler, get_gmail_pubsub_handler

    queue = FakeQueue()
    handler = GmailPubSubHandler(
        connections=FakeConnections([_connection()]),
        queue=queue,
        verifier=FakeVerifier(
            {
                "iss": "accounts.google.com",
                "aud": "https://relay.example/v1/events/gmail",
                "email": "relay-push@example.iam.gserviceaccount.com",
                "email_verified": True,
            }
        ),
        audience="https://relay.example/v1/events/gmail",
        service_account_email="relay-push@example.iam.gserviceaccount.com",
    )
    app.dependency_overrides[get_gmail_pubsub_handler] = lambda: handler
    try:
        response = TestClient(app).post(
            "/v1/events/gmail",
            headers={"Authorization": "Bearer valid-token"},
            json=_envelope(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert len(queue.commands) == 1
    assert queue.commands[0].user_id == "u1"
    assert queue.commands[0].notification.history_id == 900


class FakeAudit:
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    async def append_ingestion_audit(self, *, user_id, outcome, correlation_id, source_event_key=None):
        self.outcomes.append(outcome)


def _handler(connections, queue, claims, audit=None):
    from app.routes.pubsub import GmailPubSubHandler

    return GmailPubSubHandler(
        connections=connections,
        queue=queue,
        verifier=FakeVerifier(claims),
        audience="https://relay.example/v1/events/gmail",
        service_account_email="relay-push@example.iam.gserviceaccount.com",
        audit=audit,
    )


VALID_CLAIMS = {
    "iss": "accounts.google.com",
    "aud": "https://relay.example/v1/events/gmail",
    "email": "relay-push@example.iam.gserviceaccount.com",
    "email_verified": True,
}


def _post(handler, **kwargs):
    from app.routes.pubsub import get_gmail_pubsub_handler

    app.dependency_overrides[get_gmail_pubsub_handler] = lambda: handler
    try:
        return TestClient(app).post(
            "/v1/events/gmail",
            headers={"Authorization": "Bearer token"},
            json=_envelope(**kwargs),
        )
    finally:
        app.dependency_overrides.clear()


def test_push_rejects_an_unverified_push_identity() -> None:
    response = _post(
        _handler(
            FakeConnections([_connection()]),
            FakeQueue(),
            {**VALID_CLAIMS, "email_verified": False},
        )
    )

    assert response.status_code == 401


def test_push_acks_and_audits_an_unknown_mailbox_without_queueing() -> None:
    queue, audit = FakeQueue(), FakeAudit()

    response = _post(
        _handler(FakeConnections([_connection()]), queue, VALID_CLAIMS, audit),
        email_address="stranger@example.test",
    )

    assert response.status_code == 204
    assert queue.commands == []
    assert audit.outcomes == ["GMAIL_PUSH_MAILBOX_UNRESOLVED"]


def test_push_acks_and_audits_an_ambiguous_mailbox_without_queueing() -> None:
    queue, audit = FakeQueue(), FakeAudit()
    second = _connection().model_copy(update={"user_id": "u2"})

    response = _post(
        _handler(FakeConnections([_connection(), second]), queue, VALID_CLAIMS, audit)
    )

    assert response.status_code == 204
    assert queue.commands == []
    assert audit.outcomes == ["GMAIL_PUSH_MAILBOX_AMBIGUOUS"]


def test_push_rejects_a_malformed_envelope_without_retrying() -> None:
    from app.routes.pubsub import get_gmail_pubsub_handler

    handler = _handler(FakeConnections([_connection()]), FakeQueue(), VALID_CLAIMS)
    app.dependency_overrides[get_gmail_pubsub_handler] = lambda: handler
    try:
        response = TestClient(app).post(
            "/v1/events/gmail",
            headers={"Authorization": "Bearer token"},
            json={"message": {"data": "not-base64!!", "messageId": "pubsub-2"}},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400


class FakeMaintenance:
    def __init__(self) -> None:
        self.runs = 0

    async def run_daily_maintenance(self):
        self.runs += 1
        return {"purged": 0, "watches_renewed": 0}


def _maintenance_handler(claims, maintenance):
    from app.routes.pubsub import MaintenanceHandler

    return MaintenanceHandler(
        maintenance=maintenance,
        verifier=FakeVerifier(claims),
        audience="https://relay.example/v1/events/gmail",
        service_account_email="relay-push@example.iam.gserviceaccount.com",
    )


def _post_maintenance(handler):
    from app.routes.pubsub import get_maintenance_handler

    app.dependency_overrides[get_maintenance_handler] = lambda: handler
    try:
        return TestClient(app).post(
            "/internal/maintenance/daily", headers={"Authorization": "Bearer token"}
        )
    finally:
        app.dependency_overrides.clear()


def test_daily_cleanup_requires_the_configured_service_identity() -> None:
    maintenance = FakeMaintenance()

    response = _post_maintenance(
        _maintenance_handler({**VALID_CLAIMS, "email": "someone@else.test"}, maintenance)
    )

    assert response.status_code == 401
    assert maintenance.runs == 0


def test_daily_cleanup_runs_for_the_configured_service_identity() -> None:
    maintenance = FakeMaintenance()

    response = _post_maintenance(_maintenance_handler(VALID_CLAIMS, maintenance))

    assert response.status_code == 204
    assert maintenance.runs == 1
