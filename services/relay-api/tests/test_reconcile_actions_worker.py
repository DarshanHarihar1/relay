from __future__ import annotations

import base64
import json

import pytest

from app.workers.reconcile_actions import ReconcileActionsWorker


class FakeVerifier:
    async def verify(self, token: str, audience: str) -> dict[str, object]:
        assert token == "signed-token"
        return {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "email": "relay-worker@example.iam.gserviceaccount.com",
            "email_verified": True,
        }


class FakeReconciliation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def reconcile(self, action_id: str, *, user_id: str, correlation_id: str):
        self.calls.append((user_id, action_id))

    async def reconcile_due(self, *, user_id: str, limit: int, correlation_id: str):
        self.calls.append((user_id, f"due:{limit}"))


def body(payload: dict[str, object]) -> dict[str, object]:
    return {
        "message": {
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
            "messageId": "message-1",
        }
    }


@pytest.mark.asyncio
async def test_reconcile_worker_authenticates_and_handles_one_action() -> None:
    reconciliation = FakeReconciliation()
    worker = ReconcileActionsWorker(
        reconciliation=reconciliation,
        verifier=FakeVerifier(),
        audience="https://worker.example",
        service_account_email="relay-worker@example.iam.gserviceaccount.com",
    )

    await worker.handle_pubsub_push(
        authorization="Bearer signed-token",
        body=body({"user_id": "user-1", "action_id": "call-1"}),
        correlation_id="corr-1",
    )

    assert reconciliation.calls == [("user-1", "call-1")]


@pytest.mark.asyncio
async def test_reconcile_worker_enumerates_a_bounded_tick() -> None:
    reconciliation = FakeReconciliation()
    worker = ReconcileActionsWorker(
        reconciliation=reconciliation,
        verifier=FakeVerifier(),
        audience="https://worker.example",
        service_account_email="relay-worker@example.iam.gserviceaccount.com",
    )

    await worker.handle_pubsub_push(
        authorization="Bearer signed-token",
        body=body({"user_id": "user-1"}),
        correlation_id="corr-1",
    )

    assert reconciliation.calls == [("user-1", "due:100")]
