from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from app.adapters.errors import RetryableProviderError, TerminalProviderError
from app.domain.ingestion import GmailNotification, GoogleConnection
from app.services.gmail_ingestion import IngestGmailNotification


logger = logging.getLogger("relay.ingestion")

router = APIRouter(tags=["events"])

# Google mints push tokens with either spelling of the issuer claim.
_GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class OidcVerifier(Protocol):
    async def verify(self, token: str, audience: str) -> dict[str, Any]: ...


class GmailConnectionDirectory(Protocol):
    async def get_active_connections_by_gmail_email(
        self, email_address: str
    ) -> list[GoogleConnection]: ...


class IngestionRunner(Protocol):
    async def ingest_gmail_notification(self, command: IngestGmailNotification) -> Any: ...


class IngestionAudit(Protocol):
    async def append_ingestion_audit(
        self,
        *,
        user_id: str,
        outcome: str,
        correlation_id: str,
        source_event_key: str | None = None,
    ) -> None: ...


class GoogleOidcVerifier:
    """Verifies a Google-signed push token against Google's public keys."""

    async def verify(self, token: str, audience: str) -> dict[str, Any]:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        return id_token.verify_oauth2_token(token, google_requests.Request(), audience)


async def verify_google_service_identity(
    *,
    authorization: str | None,
    verifier: OidcVerifier,
    audience: str,
    service_account_email: str,
) -> None:
    """Accept a request only from the configured Google service identity."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401)
    try:
        claims = await verifier.verify(token, audience)
    except Exception as error:  # noqa: BLE001 - any verification failure is a rejection
        raise HTTPException(status_code=401) from error
    if (
        claims.get("iss") not in _GOOGLE_ISSUERS
        or claims.get("aud") != audience
        or claims.get("email") != service_account_email
        or claims.get("email_verified") is not True
    ):
        raise HTTPException(status_code=401)


class MaintenanceHandler:
    """The authenticated trigger for the daily cleanup and watch renewal."""

    def __init__(
        self,
        *,
        maintenance: Any,
        verifier: OidcVerifier,
        audience: str,
        service_account_email: str,
    ) -> None:
        self._maintenance = maintenance
        self._verifier = verifier
        self._audience = audience
        self._service_account_email = service_account_email

    async def run(self, *, authorization: str | None) -> None:
        await verify_google_service_identity(
            authorization=authorization,
            verifier=self._verifier,
            audience=self._audience,
            service_account_email=self._service_account_email,
        )
        await self._maintenance.run_daily_maintenance()


class GmailPubSubHandler:
    """The authenticated Pub/Sub push boundary for Gmail notifications.

    Nothing in the push payload selects a Relay user. The mailbox address is
    matched against exactly one server-side connection, and an unknown or
    ambiguous mailbox is acknowledged and audited rather than processed.
    """

    def __init__(
        self,
        *,
        connections: GmailConnectionDirectory,
        ingestion: IngestionRunner,
        verifier: OidcVerifier,
        audience: str,
        service_account_email: str,
        audit: IngestionAudit | None = None,
    ) -> None:
        self._connections = connections
        self._ingestion = ingestion
        self._verifier = verifier
        self._audience = audience
        self._service_account_email = service_account_email
        self._audit = audit

    async def handle_pubsub_push(
        self, *, authorization: str | None, body: dict[str, Any], correlation_id: str
    ) -> None:
        await self._verify_push_identity(authorization)
        notification, message_id = self._decode(body)
        correlation_id = message_id or correlation_id

        matches = await self._connections.get_active_connections_by_gmail_email(
            notification.email_address
        )
        if len(matches) != 1:
            outcome = (
                "GMAIL_PUSH_MAILBOX_AMBIGUOUS" if matches else "GMAIL_PUSH_MAILBOX_UNRESOLVED"
            )
            # Acknowledged on purpose: redelivering a mailbox Relay cannot resolve
            # would retry until the dead-letter limit without ever succeeding.
            logger.warning("gmail_push_unmapped", extra={"outcome": outcome})
            await self._append_audit(outcome, correlation_id)
            return

        command = IngestGmailNotification(
            user_id=matches[0].user_id,
            notification=notification,
            correlation_id=correlation_id,
        )
        try:
            await self._ingestion.ingest_gmail_notification(command)
        except RetryableProviderError as error:
            # Do not acknowledge. Pub/Sub redelivers under the subscription's own
            # retry policy (10s-600s backoff, 5 attempts, then the dead-letter
            # topic), which survives an instance being scaled away mid-flight.
            logger.warning("gmail_ingestion_retry", extra={"correlation_id": correlation_id})
            raise HTTPException(status_code=503) from error
        except TerminalProviderError:
            # Acknowledged on purpose: redelivering cannot make this succeed.
            logger.warning("gmail_ingestion_terminal", extra={"correlation_id": correlation_id})
            await self._append_audit("GMAIL_INGESTION_TERMINAL", correlation_id)

    async def _verify_push_identity(self, authorization: str | None) -> None:
        await verify_google_service_identity(
            authorization=authorization,
            verifier=self._verifier,
            audience=self._audience,
            service_account_email=self._service_account_email,
        )

    @staticmethod
    def _decode(body: dict[str, Any]) -> tuple[GmailNotification, str | None]:
        message = body.get("message")
        if not isinstance(message, dict):
            raise HTTPException(status_code=400)
        data = message.get("data")
        if not isinstance(data, str):
            raise HTTPException(status_code=400)
        try:
            payload = json.loads(base64.b64decode(data, validate=True))
        except (binascii.Error, ValueError) as error:
            raise HTTPException(status_code=400) from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400)
        try:
            notification = GmailNotification(
                email_address=payload["emailAddress"],
                history_id=int(payload["historyId"]),
                published_at=_published_at(message.get("publishTime")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=400) from error
        message_id = message.get("messageId")
        return notification, message_id if isinstance(message_id, str) and message_id else None

    async def _append_audit(self, outcome: str, correlation_id: str) -> None:
        if self._audit is None:
            return
        # No mailbox address or user is recorded for an unresolved push.
        await self._audit.append_ingestion_audit(
            user_id="unresolved", outcome=outcome, correlation_id=correlation_id
        )


def _published_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@lru_cache(maxsize=1)
def get_gmail_pubsub_handler() -> GmailPubSubHandler:
    from os import getenv

    from app.adapters.gemini import VertexGeminiExtractor, default_access_token_provider
    from app.adapters.gmail import GmailAdapter
    from app.routes.google import (
        get_firestore_client,
        get_gmail_ingestion_repository,
        get_google_oauth_service,
    )
    from app.services.gmail_ingestion import GmailIngestionService
    from app.adapters.pubsub_publisher import PubSubCommandPublisher
    from app.repositories.disruptions import FirestoreDisruptionRepository, FirestoreOutbox
    from app.security import FernetFieldCipher
    from app.services.commitment_matcher import ConservativeCommitmentMatcher
    from app.settings import GoogleOAuthSettings

    settings = GoogleOAuthSettings.from_env()
    service_account_email = getenv("GOOGLE_PUBSUB_PUSH_SERVICE_ACCOUNT")
    if not service_account_email:
        raise RuntimeError("Missing configuration: GOOGLE_PUBSUB_PUSH_SERVICE_ACCOUNT")
    oauth = get_google_oauth_service()
    repository = get_gmail_ingestion_repository()
    project = getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("Missing configuration: GOOGLE_CLOUD_PROJECT")
    encryption_key = getenv("APP_ENCRYPTION_KEY")
    if not encryption_key:
        raise RuntimeError("Missing configuration: APP_ENCRYPTION_KEY")
    model = getenv("RELAY_GEMINI_MODEL", "gemini-2.5-flash")
    firestore = get_firestore_client()
    disruptions = FirestoreDisruptionRepository(firestore)
    access_token_provider = default_access_token_provider()
    outbox = FirestoreOutbox(
        firestore,
        publisher=PubSubCommandPublisher(
            project=project,
            topic=getenv("RELAY_WORK_TOPIC", "relay-work"),
            access_token_provider=access_token_provider,
        ),
    )
    ingestion = GmailIngestionService(
        repository=repository,
        gmail=GmailAdapter(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            topic=settings.gmail_topic,
            refresh_token_reader=oauth.decrypt_refresh_token,
        ),
        extractor=VertexGeminiExtractor(
            project=project,
            location=getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
            model=model,
            access_token_provider=access_token_provider,
        ),
        matcher=ConservativeCommitmentMatcher(
            commitments=disruptions,
            disruptions=disruptions,
            cipher=FernetFieldCipher(encryption_key),
            model_version=model,
        ),
        phase3=outbox,
    )
    return GmailPubSubHandler(
        connections=oauth,
        ingestion=ingestion,
        verifier=GoogleOidcVerifier(),
        audience=settings.pubsub_push_audience,
        service_account_email=service_account_email,
    )


@lru_cache(maxsize=1)
def get_maintenance_handler() -> MaintenanceHandler:
    from os import getenv

    from app.adapters.gemini import default_access_token_provider
    from app.adapters.pubsub_publisher import PubSubCommandPublisher
    from app.repositories.disruptions import FirestoreOutbox
    from app.repositories.retention import FirestoreRetentionStore
    from app.routes.google import get_firestore_client, get_gmail_watch_service
    from app.services.retention import RetentionService
    from app.settings import GoogleOAuthSettings
    from app.worker import DailyMaintenance

    settings = GoogleOAuthSettings.from_env()
    service_account_email = getenv("GOOGLE_PUBSUB_PUSH_SERVICE_ACCOUNT")
    if not service_account_email:
        raise RuntimeError("Missing configuration: GOOGLE_PUBSUB_PUSH_SERVICE_ACCOUNT")
    project = getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("Missing configuration: GOOGLE_CLOUD_PROJECT")
    firestore = get_firestore_client()
    return MaintenanceHandler(
        maintenance=DailyMaintenance(
            retention=RetentionService(store=FirestoreRetentionStore(firestore)),
            watches=get_gmail_watch_service(),
            outbox=FirestoreOutbox(
                firestore,
                publisher=PubSubCommandPublisher(
                    project=project,
                    topic=getenv("RELAY_WORK_TOPIC", "relay-work"),
                    access_token_provider=default_access_token_provider(),
                ),
            ),
        ),
        verifier=GoogleOidcVerifier(),
        audience=settings.pubsub_push_audience,
        service_account_email=service_account_email,
    )


@router.post("/internal/maintenance/daily", status_code=204, response_class=Response)
async def run_daily_maintenance(
    authorization: str | None = Header(default=None),
    handler: MaintenanceHandler = Depends(get_maintenance_handler),
) -> Response:
    await handler.run(authorization=authorization)
    return Response(status_code=204)


@router.post("/v1/events/gmail", status_code=204, response_class=Response)
async def receive_gmail_push(
    request: Request,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    handler: GmailPubSubHandler = Depends(get_gmail_pubsub_handler),
) -> Response:
    await handler.handle_pubsub_push(
        authorization=authorization,
        body=body,
        correlation_id=getattr(request.state, "correlation_id", "gmail-push"),
    )
    return Response(status_code=204)


__all__ = [
    "GmailPubSubHandler",
    "GoogleOidcVerifier",
    "MaintenanceHandler",
    "get_gmail_pubsub_handler",
    "get_maintenance_handler",
    "router",
    "verify_google_service_identity",
]
