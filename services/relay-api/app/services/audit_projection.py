from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.domain.product import ActionAuditView, AuditEventView
from app.repositories.product import ProductRepository
from app.services.dashboard_projection import _action_outcome


class AuditProjectionService:
    def __init__(self, repository: ProductRepository, now: Callable[[], datetime] | None = None) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def get_action_audit(self, *, user_id: str, action_id: str) -> ActionAuditView:
        source = await self._repository.get_action_audit_source(user_id=user_id, action_id=action_id)
        if source is None:
            raise LookupError
        events = tuple(
            AuditEventView(
                occurred_at=_utc(audit.created_at),
                event_code=_event_code(audit.outcome),
                summary=_event_summary(audit.outcome),
            )
            for audit in sorted(source.audits, key=lambda item: (item.created_at, item.id))
        )
        return ActionAuditView(outcome=_action_outcome(source.action), events=events)


def _event_code(outcome: str) -> str:
    safe_codes = {
        "approval_approve": "APPROVAL_RECORDED",
        "approval_decline": "APPROVAL_DECLINED",
        "PICKUP_DECLARED_NO": "PICKUP_DECLARED_NO",
        "PICKUP_CONTACT_SELECTED": "PICKUP_CONTACT_SELECTED",
        "duplicate_ignored": "DUPLICATE_IGNORED",
    }
    return safe_codes.get(outcome, "ACTION_UPDATED")


def _event_summary(outcome: str) -> str:
    if outcome == "approval_approve":
        return "Limited action approval recorded."
    if outcome == "approval_decline":
        return "Action approval declined."
    if outcome == "PICKUP_DECLARED_NO":
        return "No pickup contact was declared."
    if outcome == "PICKUP_CONTACT_SELECTED":
        return "A pickup contact was explicitly selected."
    if outcome == "duplicate_ignored":
        return "A repeated event was ignored safely."
    return "The action state changed."


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = ["AuditProjectionService"]
