from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.adapters.errors import RetryableProviderError, TerminalProviderError
from app.providers.webhooks import WebhookVerificationError
from app.security import redact_for_log
from app.services.retry_policy import ProviderFailure


logger = logging.getLogger("relay.observability")
metrics_logger = logging.getLogger("relay.metrics")


class RelayLogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: str
    severity: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    correlation_id: str
    user_id_hash: str | None
    action_id: str | None
    approval_id: str | None
    provider: Literal["gmail", "calendar", "vapi", "fcm", "none"]
    outcome: str
    latency_ms: int | None


_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
_ACTION_COUNTERS: Counter[tuple[str, str, str]] = Counter()
_ACTION_LATENCIES: defaultdict[str, list[int]] = defaultdict(list)


def log_event(event: RelayLogEvent, **safe_fields: Any) -> None:
    payload = redact_for_log({**event.model_dump(mode="json"), **safe_fields})
    logger.log(
        _LEVELS[event.severity],
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
    )


def classify_exception(exc: Exception) -> Literal["transient", "terminal", "security", "unknown"]:
    if isinstance(exc, (WebhookVerificationError, PermissionError)):
        return "security"
    if isinstance(exc, RetryableProviderError):
        return "transient"
    if isinstance(exc, TerminalProviderError):
        return "terminal"
    if isinstance(exc, ProviderFailure):
        if exc.timed_out or exc.status_code in {408, 429} or (
            exc.status_code is not None and 500 <= exc.status_code <= 599
        ):
            return "transient"
        return "terminal"
    return "unknown"


def record_action_metric(
    *, action_type: str, state: str, provider: str, latency_ms: int | None
) -> None:
    _ACTION_COUNTERS[(action_type, state, provider)] += 1
    if latency_ms is not None and latency_ms >= 0:
        _ACTION_LATENCIES[action_type].append(latency_ms)
    metrics_logger.info(
        json.dumps(
            redact_for_log(
                {
                    "event": "action_metric",
                    "action_type": action_type,
                    "state": state,
                    "provider": provider,
                    "latency_ms": latency_ms,
                }
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
    )


__all__ = [
    "RelayLogEvent",
    "classify_exception",
    "log_event",
    "record_action_metric",
]
