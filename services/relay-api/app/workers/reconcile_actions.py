from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.routes.pubsub import OidcVerifier, verify_google_service_identity


class ReconciliationRunner(Protocol):
    async def reconcile(
        self,
        action_id: str,
        *,
        user_id: str,
        correlation_id: str,
    ) -> Any: ...

    async def reconcile_due(
        self,
        *,
        user_id: str,
        limit: int,
        correlation_id: str,
    ) -> Any: ...


class ReconcileActionsWorker:
    def __init__(
        self,
        *,
        reconciliation: ReconciliationRunner,
        verifier: OidcVerifier,
        audience: str,
        service_account_email: str,
    ) -> None:
        self._reconciliation = reconciliation
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
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=400)
        message_id = body.get("message", {}).get("messageId")
        correlation = message_id if isinstance(message_id, str) and message_id else correlation_id
        action_id = payload.get("action_id")
        if isinstance(action_id, str) and action_id:
            await self._reconciliation.reconcile(
                action_id,
                user_id=user_id,
                correlation_id=correlation,
            )
        else:
            await self._reconciliation.reconcile_due(
                user_id=user_id,
                limit=100,
                correlation_id=correlation,
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


async def get_reconcile_actions_worker(request: Request) -> ReconcileActionsWorker:
    worker = getattr(request.app.state, "reconcile_actions_worker", None)
    if worker is None:
        raise HTTPException(status_code=503)
    return worker


@router.post("/internal/pubsub/relay-reconcile", status_code=204, response_class=Response)
async def receive_reconcile_tick(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    worker: ReconcileActionsWorker = Depends(get_reconcile_actions_worker),
) -> Response:
    await worker.handle_pubsub_push(
        authorization=authorization,
        body=body,
        correlation_id=getattr(request.state, "correlation_id", "reconcile"),
    )
    return Response(status_code=204)


__all__ = ["ReconcileActionsWorker", "get_reconcile_actions_worker", "router"]
