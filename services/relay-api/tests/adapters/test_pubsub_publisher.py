from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.services.retention import AssessDisruption


COMMAND = AssessDisruption(
    disruption_id="d1",
    commitment_id="flight_AB12",
    correlation_id="corr-1",
    source_event_key="gmail:m1:900",
)


def _publisher(handler):
    from app.adapters.pubsub_publisher import PubSubCommandPublisher

    return PubSubCommandPublisher(
        project="relay-test",
        topic="relay-work",
        access_token_provider=lambda: "ya29.token",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_the_command_is_published_to_the_configured_topic() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messageIds": ["1"]})

    await _publisher(handler).publish(COMMAND)

    assert captured["url"] == (
        "https://pubsub.googleapis.com/v1/projects/relay-test/topics/relay-work:publish"
    )
    assert captured["auth"] == "Bearer ya29.token"
    message = captured["body"]["messages"][0]
    assert json.loads(base64.b64decode(message["data"])) == COMMAND.model_dump(mode="json")
    # The disruption ID is the consumer's deduplication key.
    assert message["attributes"] == {"disruption_id": "d1", "correlation_id": "corr-1"}


@pytest.mark.asyncio
async def test_a_published_command_carries_no_message_content() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["raw"] = request.content.decode()
        return httpx.Response(200, json={"messageIds": ["1"]})

    await _publisher(handler).publish(COMMAND)

    for leaked in ("subject", "text_body", "evidence", "booking", "@"):
        assert leaked not in captured["raw"].lower()


@pytest.mark.asyncio
async def test_a_transient_publish_failure_is_retryable() -> None:
    from app.adapters.errors import RetryableProviderError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    with pytest.raises(RetryableProviderError):
        await _publisher(handler).publish(COMMAND)


@pytest.mark.asyncio
async def test_a_rejected_publish_is_terminal() -> None:
    from app.adapters.errors import TerminalProviderError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(TerminalProviderError):
        await _publisher(handler).publish(COMMAND)
