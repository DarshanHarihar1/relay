from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx

from app.adapters.errors import RetryableProviderError, TerminalProviderError
from app.services.retention import AssessDisruption


_PUBSUB_URL = "https://pubsub.googleapis.com/v1/projects"


class PubSubCommandPublisher:
    """Publishes a Phase 3 assessment command over the Pub/Sub REST API.

    The message body is the command itself, which carries identifiers only, so
    nothing derived from the user's mail ever reaches the topic.
    """

    def __init__(
        self,
        *,
        project: str,
        topic: str,
        access_token_provider: Callable[[], str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._project = project
        self._topic = topic
        self._access_token_provider = access_token_provider
        self._transport = transport
        self._timeout = timeout

    async def publish(self, command: AssessDisruption) -> None:
        payload = json.dumps(command.model_dump(mode="json")).encode("utf-8")
        body = {
            "messages": [
                {
                    "data": base64.b64encode(payload).decode("ascii"),
                    # The consumer deduplicates on disruption_id.
                    "attributes": {
                        "disruption_id": command.disruption_id,
                        "correlation_id": command.correlation_id,
                    },
                }
            ]
        }
        url = f"{_PUBSUB_URL}/{self._project}/topics/{self._topic}:publish"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {self._access_token_provider()}"},
                )
        except httpx.TimeoutException as error:
            raise RetryableProviderError("Pub/Sub publish timed out") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableProviderError(f"Pub/Sub unavailable: {response.status_code}")
        if response.status_code >= 400:
            raise TerminalProviderError(f"Pub/Sub rejected the publish: {response.status_code}")


__all__ = ["PubSubCommandPublisher"]
