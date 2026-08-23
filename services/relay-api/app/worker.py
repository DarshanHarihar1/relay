from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, cast

from app.services.notifications import NotificationKind, NotificationService


logger = logging.getLogger("relay.worker")

# Retries are owned by the Pub/Sub subscription, not by this process: see
# infra/gcp/pubsub.sh, which configures 10s-600s backoff, five delivery
# attempts, and the relay-dead-letter topic. A push handler that slept here
# would hold the request past the acknowledgement deadline.


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


class DashboardNotificationWorker:
    """Delivers dashboard notifications from an already-committed outbox event."""

    def __init__(self, *, notifications: NotificationService) -> None:
        self._notifications = notifications

    async def handle_outbox_event(self, event: Mapping[str, object]) -> int:
        user_id = event.get("user_id")
        kind = event.get("kind")
        entity_id = event.get("entity_id")
        correlation_id = event.get("correlation_id")
        if (
            not isinstance(user_id, str)
            or not isinstance(kind, str)
            or kind not in {"approval_needed", "outcome_updated"}
            or not isinstance(entity_id, str)
            or not isinstance(correlation_id, str)
        ):
            raise ValueError("Invalid dashboard notification event")
        return await self._notifications.notify_dashboard_change(
            user_id=user_id,
            kind=cast(NotificationKind, kind),
            entity_id=entity_id,
            correlation_id=correlation_id,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["DailyMaintenance", "DashboardNotificationWorker"]
