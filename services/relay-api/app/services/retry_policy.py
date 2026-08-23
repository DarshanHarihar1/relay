from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.contracts import ActionRecord, ActionState


class ProviderFailure(Exception):
    """A classified provider failure, safe to persist without raw provider data."""

    def __init__(
        self,
        message: str = "provider failure",
        *,
        status_code: int | None = None,
        timed_out: bool = False,
        authorization_failure: bool = False,
        validation_failure: bool = False,
        unknown_outcome: bool = False,
        out_of_bounds: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.timed_out = timed_out
        self.authorization_failure = authorization_failure
        self.validation_failure = validation_failure
        self.unknown_outcome = unknown_outcome
        self.out_of_bounds = out_of_bounds


@dataclass(frozen=True)
class RetryDecision:
    state: ActionState
    delay_seconds: int
    reason: str
    attempt: int


class RetryPolicy:
    _MAX_ATTEMPTS = 3
    _CAPS_SECONDS = (30, 120, 600)

    def next_attempt(
        self,
        action: ActionRecord,
        error: ProviderFailure,
        *,
        now: datetime,
    ) -> RetryDecision:
        attempt = action.retry_count + 1
        if (
            error.authorization_failure
            or error.validation_failure
            or error.unknown_outcome
            or error.out_of_bounds
            or not self._is_transient(error)
        ):
            return RetryDecision(ActionState.NEEDS_USER, 0, "provider_failure_requires_user", attempt)
        if attempt > self._MAX_ATTEMPTS:
            return RetryDecision(ActionState.FAILED, 0, "retry_limit_exhausted", attempt)

        cap = self._CAPS_SECONDS[attempt - 1]
        delay = int(random.uniform(0, cap))
        expiry = action.expires_at
        if action.type == "voice_call":
            expiry = expiry or action.authorization_snapshot.expires_at
        if expiry is not None and now + timedelta(seconds=delay) >= expiry:
            return RetryDecision(ActionState.NEEDS_USER, 0, "authorization_expires_before_retry", attempt)
        return RetryDecision(ActionState.RETRYABLE_FAILURE, delay, "transient_provider_failure", attempt)

    @staticmethod
    def _is_transient(error: ProviderFailure) -> bool:
        return error.timed_out or error.status_code in {408, 429} or (
            error.status_code is not None and 500 <= error.status_code <= 599
        )


__all__ = ["ProviderFailure", "RetryDecision", "RetryPolicy"]
