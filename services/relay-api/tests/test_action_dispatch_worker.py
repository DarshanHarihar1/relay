from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.workers.action_dispatch import ActionDispatchWorker, ActionWorkPublisher


class FakeVerifier:
    async def verify(self, token: str, audience: str) -> dict[str, object]:
        assert token == "signed-token"
        assert audience == "https://worker.example"
        return {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "email": "relay-worker@example.iam.gserviceaccount.com",
            "email_verified": True,
        }


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def dispatch(self, user_id: str, action_id: str, *, correlation_id: str):
        self.calls.append((user_id, action_id))
        return None


def push_body() -> dict[str, object]:
    payload = json.dumps({"user_id": "user-1", "action_id": "call-1"}).encode()
    return {
        "message": {
            "data": base64.b64encode(payload).decode(),
            "messageId": "message-1",
        }
    }


@pytest.mark.asyncio
async def test_action_worker_verifies_identity_and_dispatches_identifier_only_message() -> None:
    dispatcher = FakeDispatcher()
    worker = ActionDispatchWorker(
        dispatcher=dispatcher,
        verifier=FakeVerifier(),
        audience="https://worker.example",
        service_account_email="relay-worker@example.iam.gserviceaccount.com",
    )

    await worker.handle_pubsub_push(
        authorization="Bearer signed-token",
        body=push_body(),
        correlation_id="corr-1",
    )

    assert dispatcher.calls == [("user-1", "call-1")]


@pytest.mark.asyncio
async def test_action_worker_rejects_unsigned_push() -> None:
    worker = ActionDispatchWorker(
        dispatcher=FakeDispatcher(),
        verifier=FakeVerifier(),
        audience="https://worker.example",
        service_account_email="relay-worker@example.iam.gserviceaccount.com",
    )

    with pytest.raises(Exception):
        await worker.handle_pubsub_push(
            authorization=None,
            body=push_body(),
            correlation_id="corr-1",
        )


@pytest.mark.asyncio
async def test_action_work_publisher_sends_only_scoped_identifiers() -> None:
    requests: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request)

    publisher = ActionWorkPublisher(
        project="relay-test",
        access_token_provider=lambda: "token",
        transport=httpx.MockTransport(transport),
    )

    await publisher.publish(user_id="user-1", action_id="call-1", correlation_id="corr-1")

    body = json.loads(requests[0].content)
    encoded = body["messages"][0]["data"]
    assert json.loads(base64.b64decode(encoded)) == {"user_id": "user-1", "action_id": "call-1"}
    assert "provider_ref" not in json.dumps(body)
