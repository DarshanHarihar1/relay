from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.adapters.errors import RetryableProviderError, TerminalProviderError
from app.repositories.actions import ActionRepository
from app.routes.pubsub import OidcVerifier, verify_google_service_identity


class DispatchRunner(Protocol):
    async def dispatch(
        self,
        user_id: str,
        action_id: str,
        *,
        correlation_id: str,
    ) -> Any: ...


class ActionWorkPublisher:
    """Publishes opaque, user-scoped action identifiers after a commit."""

    def __init__(
        self,
        *,
        project: str,
        topic: str = "relay-action-work",
        access_token_provider: Callable[[], str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._project = project
        self._topic = topic
        self._access_token_provider = access_token_provider
        self._transport = transport
        self._timeout = timeout

    async def publish(self, *, user_id: str, action_id: str, correlation_id: str) -> None:
        payload = json.dumps(
            {"user_id": user_id, "action_id": action_id},
            separators=(",", ":"),
        ).encode()
        body = {
            "messages": [
                {
                    "data": base64.b64encode(payload).decode("ascii"),
                    "attributes": {"correlation_id": correlation_id},
                }
            ]
        }
        url = f"https://pubsub.googleapis.com/v1/projects/{self._project}/topics/{self._topic}:publish"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {self._access_token_provider()}"},
                )
        except httpx.TimeoutException as error:
            raise RetryableProviderError("Action work publish timed out") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableProviderError(f"Action work publish unavailable: {response.status_code}")
        if response.status_code >= 400:
            raise TerminalProviderError(f"Action work publish rejected: {response.status_code}")

    async def publish_pending(
        self,
        *,
        repository: ActionRepository,
        user_id: str,
        limit: int = 100,
        correlation_id: str = "action-republish",
    ) -> int:
        action_ids = await repository.list_pending_dispatches(user_id, limit)
        for action_id in action_ids:
            await self.publish(user_id=user_id, action_id=action_id, correlation_id=correlation_id)
        return len(action_ids)


class ActionDispatchWorker:
    def __init__(
        self,
        *,
        dispatcher: DispatchRunner,
        verifier: OidcVerifier,
        audience: str,
        service_account_email: str,
    ) -> None:
        self._dispatcher = dispatcher
        self._verifier = verifier
        self._audience = audience
        self._service_account_email = service_account_email

    async def handle_pubsub_push(
        self,
        *,
        authorization: str | None,
        body: dict[str, Any],
        correlation_id: str,
    ) -> None:
        await verify_google_service_identity(
            authorization=authorization,
            verifier=self._verifier,
            audience=self._audience,
            service_account_email=self._service_account_email,
        )
        payload = self._decode(body)
        user_id = payload.get("user_id")
        action_id = payload.get("action_id")
        if not isinstance(user_id, str) or not user_id or not isinstance(action_id, str) or not action_id:
            raise HTTPException(status_code=400)
        message_id = body.get("message", {}).get("messageId")
        await self._dispatcher.dispatch(
            user_id,
            action_id,
            correlation_id=message_id if isinstance(message_id, str) and message_id else correlation_id,
        )

    @staticmethod
    def _decode(body: dict[str, Any]) -> dict[str, Any]:
        message = body.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("data"), str):
            raise HTTPException(status_code=400)
        try:
            decoded = base64.b64decode(message["data"], validate=True)
            payload = json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, TypeError, ValueError) as error:
            raise HTTPException(status_code=400) from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400)
        return payload


router = APIRouter(tags=["internal-events"])


async def get_action_dispatch_worker(request: Request) -> ActionDispatchWorker:
    worker = getattr(request.app.state, "action_dispatch_worker", None)
    if worker is None:
        raise HTTPException(status_code=503)
    return worker


@router.post("/internal/pubsub/relay-action-work", status_code=204, response_class=Response)
async def receive_action_work(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    worker: ActionDispatchWorker = Depends(get_action_dispatch_worker),
) -> Response:
    await worker.handle_pubsub_push(
        authorization=authorization,
        body=body,
        correlation_id=getattr(request.state, "correlation_id", "action-work"),
    )
    return Response(status_code=204)


__all__ = [
    "ActionDispatchWorker",
    "ActionWorkPublisher",
    "DispatchRunner",
    "get_action_dispatch_worker",
    "receive_action_work",
    "router",
]
