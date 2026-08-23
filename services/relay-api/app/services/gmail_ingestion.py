from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from collections.abc import Callable
from typing import Protocol

from pydantic import Field

from app.adapters.errors import RetryableProviderError
from app.adapters.gemini import ExtractionReviewRequired
from app.adapters.gmail import (
    GmailHistoryExpiredError,
    GmailRetryableError,
    GmailTerminalError,
    UnsupportedGmailMessageError,
)
from app.contracts import ContractModel, SourceEventEnvelope
from app.domain.ingestion import DisruptionCandidate, GmailMessage, MatchResult
from app.domain.ingestion import (
    GmailNotification,
    GoogleConnection,
    HistoryPage,
    WatchRegistration,
)
from app.ports.google import GmailPort


class IngestGmailNotification(ContractModel):
    user_id: str = Field(min_length=1)
    notification: GmailNotification
    correlation_id: str = Field(min_length=1)


class DisruptionExtractor(Protocol):
    async def extract(
        self, *, message: GmailMessage, correlation_id: str
    ) -> DisruptionCandidate | None: ...


class CommitmentMatcher(Protocol):
    async def match(
        self, *, user_id: str, candidate: DisruptionCandidate, received_at: datetime
    ) -> MatchResult: ...

    async def create_disruption_from_match(
        self,
        *,
        user_id: str,
        message: GmailMessage,
        candidate: DisruptionCandidate,
        match: MatchResult,
        source_event_key: str,
        correlation_id: str,
    ) -> bool: ...


class IngestionSummary(ContractModel):
    persisted_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    review_count: int = Field(default=0, ge=0)
    disruption_count: int = Field(default=0, ge=0)
    ignored_count: int = Field(default=0, ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    stale: bool = False
    resynced: bool = False


class _ConnectionStore(Protocol):
    async def get_connection(self, user_id: str) -> GoogleConnection | None: ...

    async def put_connection(self, connection: GoogleConnection) -> None: ...

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]: ...


class GmailIngestionRepository(Protocol):
    async def get_connection(self, user_id: str) -> GoogleConnection | None: ...

    async def get_gmail_cursor(self, *, user_id: str, mailbox: str) -> int | None: ...

    async def update_gmail_cursor_if_newer(
        self, *, user_id: str, mailbox: str, proposed_history_id: int
    ) -> int: ...

    async def claim_source_event(self, *, user_id: str, event: SourceEventEnvelope) -> bool: ...

    async def release_source_event_claim(self, *, user_id: str, source_event_key: str) -> None: ...

    async def append_ingestion_audit(
        self,
        *,
        user_id: str,
        outcome: str,
        correlation_id: str,
        source_event_key: str | None = None,
        detail: dict[str, str] | None = None,
    ) -> None: ...

    async def put_connection(self, connection: GoogleConnection) -> None: ...

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]: ...


class GmailIngestionService:
    """Turns one Gmail notification into bounded, idempotent source events.

    Extraction and commitment mutation deliberately happen in later Phase 2 tasks.
    This service only accepts the configured label and records a minimum source
    event envelope that can safely be consumed exactly once by those tasks.
    """

    _RESYNC_WINDOW = timedelta(hours=48)

    def __init__(
        self,
        *,
        repository: GmailIngestionRepository,
        gmail: GmailPort,
        extractor: DisruptionExtractor | None = None,
        matcher: CommitmentMatcher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._gmail = gmail
        self._extractor = extractor
        self._matcher = matcher
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def ingest_gmail_notification(self, command: IngestGmailNotification) -> IngestionSummary:
        connection = await self._connection_for_command(command)
        mailbox = connection.gmail_email_address
        assert mailbox is not None
        cursor = await self._repository.get_gmail_cursor(user_id=command.user_id, mailbox=mailbox)
        if cursor is not None and command.notification.history_id <= cursor:
            return IngestionSummary(stale=True)
        if cursor is None:
            # A watch registration establishes the first cursor. If a notification
            # arrived before it was persisted, do not scan an unbounded mailbox.
            await self._repository.update_gmail_cursor_if_newer(
                user_id=command.user_id,
                mailbox=mailbox,
                proposed_history_id=command.notification.history_id,
            )
            await self._audit(command, "GMAIL_CURSOR_INITIALIZED")
            return IngestionSummary(stale=True)

        resynced = False
        try:
            pages = await self._history_pages(connection=connection, start_history_id=cursor)
        except GmailHistoryExpiredError:
            resynced = True
            await self._audit(command, "GMAIL_HISTORY_EXPIRED_RESYNC")
            pages = await self._resync_pages(connection=connection)
        except GmailTerminalError:
            await self._audit(command, "GMAIL_HISTORY_TERMINAL_FAILURE")
            raise

        summary = IngestionSummary(resynced=resynced)
        seen_message_ids: set[str] = set()
        highest_history_id = command.notification.history_id
        for page in pages:
            highest_history_id = max(highest_history_id, page.history_id)
            for message_id in page.added_message_ids:
                if message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message_id)
                source_event_key = f"gmail:{message_id}:{command.notification.history_id}"
                event = SourceEventEnvelope(
                    source="gmail",
                    source_event_key=source_event_key,
                    occurred_at=command.notification.published_at,
                    payload={"message_id": message_id, "history_id": str(command.notification.history_id)},
                    correlation_id=command.correlation_id,
                )
                claimed = await self._repository.claim_source_event(user_id=command.user_id, event=event)
                if not claimed:
                    summary.duplicate_count += 1
                    continue
                try:
                    message = await self._gmail.get_message(connection=connection, message_id=message_id)
                except GmailRetryableError:
                    await self._repository.release_source_event_claim(
                        user_id=command.user_id, source_event_key=source_event_key
                    )
                    raise
                except UnsupportedGmailMessageError:
                    summary.ignored_count += 1
                    await self._audit(command, "GMAIL_UNSUPPORTED_MIME", source_event_key)
                    continue
                except GmailTerminalError:
                    summary.ignored_count += 1
                    await self._audit(command, "GMAIL_MESSAGE_TERMINAL_FAILURE", source_event_key)
                    continue
                if connection.gmail_label_id not in message.label_ids:
                    summary.ignored_count += 1
                    await self._audit(command, "GMAIL_MESSAGE_OUTSIDE_CONFIGURED_LABEL", source_event_key)
                    continue
                # The body remains in process only. The durable event has no subject,
                # sender, body, attachment, or header content.
                summary.persisted_count += 1
                candidate = await self._extract(
                    command=command,
                    message=message,
                    source_event_key=source_event_key,
                    summary=summary,
                )
                if candidate is not None:
                    await self._match(
                        command=command,
                        message=message,
                        candidate=candidate,
                        source_event_key=source_event_key,
                        summary=summary,
                    )

        await self._repository.update_gmail_cursor_if_newer(
            user_id=command.user_id,
            mailbox=mailbox,
            proposed_history_id=highest_history_id,
        )
        return summary

    async def _extract(
        self,
        *,
        command: IngestGmailNotification,
        message: GmailMessage,
        source_event_key: str,
        summary: IngestionSummary,
    ) -> DisruptionCandidate | None:
        """Ask the model for a candidate. Model output never creates a disruption here."""
        if self._extractor is None:
            return None
        try:
            candidate = await self._extractor.extract(
                message=message, correlation_id=command.correlation_id
            )
        except ExtractionReviewRequired as review:
            # A rejected extraction is not retried and never becomes a disruption.
            summary.review_count += 1
            await self._audit(
                command,
                "EXTRACTION_REVIEW_REQUIRED",
                source_event_key,
                detail={"reason": review.reason, "message_id_hash": _message_hash(message.id)},
            )
            return None
        except RetryableProviderError:
            # The claim must be released so the bounded retry can run again.
            await self._repository.release_source_event_claim(
                user_id=command.user_id, source_event_key=source_event_key
            )
            raise
        if candidate is None:
            return None
        summary.candidate_count += 1
        return candidate

    async def _match(
        self,
        *,
        command: IngestGmailNotification,
        message: GmailMessage,
        candidate: DisruptionCandidate,
        source_event_key: str,
        summary: IngestionSummary,
    ) -> None:
        """Persist a disruption only on a decisive match. Review mutates nothing."""
        if self._matcher is None:
            return
        result = await self._matcher.match(
            user_id=command.user_id,
            candidate=candidate,
            received_at=command.notification.published_at,
        )
        if result.status != "matched":
            if result.status == "needs_review":
                summary.review_count += 1
                await self._audit(
                    command,
                    "MATCH_REVIEW_REQUIRED",
                    source_event_key,
                    detail={
                        "reasons": ",".join(result.reasons),
                        "message_id_hash": _message_hash(message.id),
                    },
                )
            return
        created = await self._matcher.create_disruption_from_match(
            user_id=command.user_id,
            message=message,
            candidate=candidate,
            match=result,
            source_event_key=source_event_key,
            correlation_id=command.correlation_id,
        )
        if created:
            summary.disruption_count += 1

    async def _connection_for_command(self, command: IngestGmailNotification) -> GoogleConnection:
        connection = await self._repository.get_connection(command.user_id)
        if connection is None or not connection.gmail_label_id or not connection.gmail_email_address:
            raise GmailTerminalError("No active Gmail connection is available")
        if connection.gmail_email_address.casefold() != command.notification.email_address.casefold():
            # The notification address is never trusted to select a user. This is
            # defence in depth behind the exact resolution performed at the push edge.
            raise GmailTerminalError("Gmail notification mailbox does not match connection")
        return connection

    async def _history_pages(self, *, connection: GoogleConnection, start_history_id: int) -> list[HistoryPage]:
        pages: list[HistoryPage] = []
        page_token: str | None = None
        while True:
            page = await self._gmail.list_history(
                connection=connection,
                start_history_id=start_history_id,
                page_token=page_token,
            )
            pages.append(page)
            if page.next_page_token is None:
                return pages
            page_token = page.next_page_token

    async def _resync_pages(self, *, connection: GoogleConnection) -> list[HistoryPage]:
        resync = getattr(self._gmail, "resync_last_48_hours", None)
        if resync is None:
            raise GmailTerminalError("Gmail history resync is not configured")
        page = await resync(connection=connection, since=self._now() - self._RESYNC_WINDOW)
        if not isinstance(page, HistoryPage):
            raise GmailTerminalError("Gmail history resync response was malformed")
        return [page]

    async def _audit(
        self,
        command: IngestGmailNotification,
        outcome: str,
        source_event_key: str | None = None,
        detail: dict[str, str] | None = None,
    ) -> None:
        await self._repository.append_ingestion_audit(
            user_id=command.user_id,
            outcome=outcome,
            correlation_id=command.correlation_id,
            source_event_key=source_event_key,
            detail=detail,
        )


class GmailWatchService:
    """Registers and renews the label-scoped Gmail watch for one connection.

    The mailbox address is read from Gmail itself so that a push payload can
    only ever be matched against a server-resolved address.
    """

    RENEWAL_LEAD = timedelta(hours=24)

    def __init__(
        self,
        *,
        repository: GmailIngestionRepository,
        gmail: GmailPort,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._gmail = gmail
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def register_gmail_watch(self, user_id: str) -> WatchRegistration:
        connection = await self._repository.get_connection(user_id)
        if connection is None:
            raise GmailTerminalError("No active Google connection is available")
        profile = await self._gmail.get_profile(connection=connection)
        registration = await self._gmail.ensure_watch(connection=connection)
        await self._repository.put_connection(
            connection.model_copy(
                update={
                    "gmail_email_address": profile.email_address,
                    "gmail_history_id": registration.history_id,
                    "gmail_watch_expires_at": registration.expires_at,
                }
            )
        )
        return registration

    async def renew_expiring_watches(self) -> list[str]:
        due_before = self._now() + self.RENEWAL_LEAD
        renewed: list[str] = []
        for connection in await self._repository.list_connections_due_for_watch_renewal(due_before):
            try:
                await self.register_gmail_watch(connection.user_id)
            except (GmailRetryableError, GmailTerminalError):
                await self._repository.append_ingestion_audit(
                    user_id=connection.user_id,
                    outcome="GMAIL_WATCH_RENEWAL_FAILED",
                    correlation_id=f"watch-renewal:{connection.user_id}",
                )
                continue
            renewed.append(connection.user_id)
        return renewed


class InMemoryGmailIngestionRepository:
    """Development-only store. Deployments replace this with Firestore.

    Connections are read through the existing server-side Google OAuth store so
    there is exactly one connection record, and the Gmail cursor lives on that
    record rather than in a second source of truth.
    """

    def __init__(self, connections: "_ConnectionStore") -> None:
        self._connections = connections
        self._claims: set[tuple[str, str]] = set()
        self.audits: list[tuple[str, str]] = []

    async def get_connection(self, user_id: str) -> GoogleConnection | None:
        return await self._connections.get_connection(user_id)

    async def put_connection(self, connection: GoogleConnection) -> None:
        await self._connections.put_connection(connection)

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]:
        return await self._connections.list_connections_due_for_watch_renewal(before)

    async def get_gmail_cursor(self, *, user_id: str, mailbox: str) -> int | None:
        connection = await self._connections.get_connection(user_id)
        if connection is None or connection.gmail_email_address != mailbox:
            return None
        return connection.gmail_history_id

    async def update_gmail_cursor_if_newer(
        self, *, user_id: str, mailbox: str, proposed_history_id: int
    ) -> int:
        connection = await self._connections.get_connection(user_id)
        if connection is None or connection.gmail_email_address != mailbox:
            raise GmailTerminalError("No active Gmail connection is available")
        current = connection.gmail_history_id or 0
        if proposed_history_id <= current:
            return current
        await self._connections.put_connection(
            connection.model_copy(update={"gmail_history_id": proposed_history_id})
        )
        return proposed_history_id

    async def claim_source_event(self, *, user_id: str, event: SourceEventEnvelope) -> bool:
        key = (user_id, event.source_event_key)
        if key in self._claims:
            return False
        self._claims.add(key)
        return True

    async def release_source_event_claim(self, *, user_id: str, source_event_key: str) -> None:
        self._claims.discard((user_id, source_event_key))

    async def append_ingestion_audit(
        self,
        *,
        user_id: str,
        outcome: str,
        correlation_id: str,
        source_event_key: str | None = None,
        detail: dict[str, str] | None = None,
    ) -> None:
        del source_event_key, detail
        self.audits.append((user_id, outcome))


def _message_hash(message_id: str) -> str:
    """A stable, redacted reference. The raw message ID never reaches an audit."""
    return sha256(message_id.encode("utf-8")).hexdigest()


__all__ = [
    "CommitmentMatcher",
    "DisruptionExtractor",
    "GmailIngestionService",
    "InMemoryGmailIngestionRepository",
    "GmailWatchService",
    "GmailRetryableError",
    "IngestGmailNotification",
    "IngestionSummary",
]
