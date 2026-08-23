from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, Protocol

from app.adapters.errors import RetryableProviderError, TerminalProviderError


logger = logging.getLogger("relay.worker")

# One bounded ladder for every retryable Gmail failure. A fourth failure
# dead-letters instead of retrying so Pub/Sub never redelivers indefinitely.
RETRY_DELAYS_SECONDS = (30, 120, 600)


class GmailIngestionRunner(Protocol):
    async def ingest_gmail_notification(self, command: Any) -> Any: ...


class DeadLetterQueue(Protocol):
    async def publish(self, command: Any, reason: str) -> None: ...


class InMemoryDeadLetterQueue:
    """Development-only sink. Deployments publish to the relay-dead-letter topic."""

    def __init__(self) -> None:
        self.items: list[Any] = []

    async def publish(self, command: Any, reason: str) -> None:
        del reason
        self.items.append(command)


class GmailWorker:
    """Runs one durable ingestion command under the bounded retry policy."""

    def __init__(
        self,
        *,
        ingestion: GmailIngestionRunner,
        dead_letters: DeadLetterQueue,
        sleep: Callable[[int], Any] | None = None,
    ) -> None:
        self._ingestion = ingestion
        self._dead_letters = dead_letters
        self._sleep = sleep or asyncio.sleep

    async def process(self, command: Any) -> Any:
        for delay in (*RETRY_DELAYS_SECONDS, None):
            try:
                return await self._ingestion.ingest_gmail_notification(command)
            except TerminalProviderError as error:
                # Terminal states are already audited by the service; retrying
                # a revoked grant or malformed mail cannot succeed.
                logger.warning("ingestion_terminal", extra={"reason": type(error).__name__})
                await self._dead_letters.publish(command, "terminal")
                return None
            except RetryableProviderError:
                if delay is None:
                    break
                await self._delay(delay)
        logger.warning("ingestion_exhausted_retries")
        await self._dead_letters.publish(command, "retries_exhausted")
        return None

    async def _delay(self, seconds: int) -> None:
        result = self._sleep(seconds)
        if inspect.isawaitable(result):
            await result


class DailyMaintenance:
    """The one scheduled job Phase 2 owns: purge expired data, renew watches.

    Both steps are idempotent, so a repeated or overlapping run is harmless.
    """

    def __init__(self, *, retention: Any, watches: Any = None, outbox: Any = None) -> None:
        self._retention = retention
        self._watches = watches
        self._outbox = outbox

    async def run_daily_maintenance(self) -> dict[str, int]:
        summary = await self._retention.purge_expired_ingestion_data(now=_utc_now())
        renewed: list[str] = []
        if self._watches is not None:
            renewed = await self._watches.renew_expiring_watches()
        # Any assessment whose immediate publish failed is still in the outbox.
        published = await self._outbox.drain() if self._outbox is not None else 0
        # Counts only. Nothing identifying reaches this log line.
        logger.info(
            "daily_maintenance",
            extra={
                "purged": summary.total(),
                "watches_renewed": len(renewed),
                "assessments_published": published,
            },
        )
        return {
            "purged": summary.total(),
            "watches_renewed": len(renewed),
            "assessments_published": published,
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LocalIngestionQueue:
    """Development-only queue that runs the worker in this process.

    Deployments publish the command to the relay-work topic instead, so the
    private worker service applies the same retry ladder out of process.
    """

    def __init__(self, worker: GmailWorker) -> None:
        self._worker = worker
        self._tasks: set[asyncio.Task[Any]] = set()

    async def enqueue(self, command: Any) -> None:
        task = asyncio.create_task(self._worker.process(command))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


__all__ = ["DailyMaintenance", "DeadLetterQueue", "LocalIngestionQueue", "GmailWorker", "InMemoryDeadLetterQueue", "RETRY_DELAYS_SECONDS"]
