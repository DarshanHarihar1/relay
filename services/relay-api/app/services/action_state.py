from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any

from app.contracts import ActionState, JsonValue


class InvalidActionTransition(ValueError):
    """Raised when a requested action state change is not part of the lifecycle."""


ALLOWED_TRANSITIONS: dict[ActionState, set[ActionState]] = {
    ActionState.PLANNED: {ActionState.AWAITING_APPROVAL},
    ActionState.AWAITING_APPROVAL: {ActionState.AUTHORIZED, ActionState.NEEDS_USER},
    ActionState.AUTHORIZED: {
        ActionState.DISPATCHED,
        ActionState.HANDOFF_OPENED,
        ActionState.NEEDS_USER,
    },
    ActionState.DISPATCHED: {
        ActionState.IN_PROGRESS,
        ActionState.RETRYABLE_FAILURE,
        ActionState.NEEDS_USER,
        ActionState.FAILED,
    },
    ActionState.IN_PROGRESS: {
        ActionState.SUCCEEDED,
        ActionState.RETRYABLE_FAILURE,
        ActionState.NEEDS_USER,
        ActionState.FAILED,
    },
    ActionState.SUCCEEDED: {
        ActionState.VERIFIED,
        ActionState.NEEDS_USER,
        ActionState.RETRYABLE_FAILURE,
    },
    ActionState.RETRYABLE_FAILURE: {
        ActionState.DISPATCHED,
        ActionState.NEEDS_USER,
        ActionState.FAILED,
    },
}


def validate_transition(
    current: ActionState,
    requested: ActionState,
    *,
    action_type: str | None = None,
) -> None:
    """Ensure that a state change preserves Relay's action lifecycle."""
    if requested is ActionState.HANDOFF_OPENED and action_type != "uber_deep_link":
        raise InvalidActionTransition("Only an Uber deep link action can open a handoff")
    if requested not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidActionTransition(f"Cannot transition action from {current.value} to {requested.value}")


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return value.isoformat()
    raise TypeError(f"Unsupported action idempotency value: {type(value).__name__}")


def derive_action_idempotency_key(
    repair_plan_version: int,
    action_type: str,
    target_ref: str,
    authorization_snapshot: Mapping[str, JsonValue],
) -> str:
    """Return the stable identity of an action's approved immutable bounds."""
    payload = [repair_plan_version, action_type, target_ref, _json_value(authorization_snapshot)]
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )
    return f"relay-action-v1:{sha256(canonical.encode('utf-8')).hexdigest()}"
