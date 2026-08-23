from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import time
from typing import Any, Protocol
from urllib.parse import parse_qsl

from app.contracts import ActionRecord, ActionState, CallContract, ProviderEvent, RecordCallOutcomeInput, validate_call_outcome
from app.providers.vapi import VapiVoiceAdapter
from app.providers.twilio import TwilioVoiceCallbackVerifier


MAX_WEBHOOK_BYTES = 256 * 1024


class WebhookVerificationError(ValueError):
    pass


class WebhookPayloadTooLarge(ValueError):
    pass


class WebhookRateLimited(ValueError):
    pass


class WebhookRateLimiter:
    def __init__(self, *, limit: int = 60, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._windows: dict[str, tuple[float, int]] = {}

    def check(self, provider: str) -> None:
        now = time.monotonic()
        started, count = self._windows.get(provider, (now, 0))
        if now - started >= self._window_seconds:
            started, count = now, 0
        if count >= self._limit:
            raise WebhookRateLimited
        self._windows[provider] = (started, count + 1)


class WebhookActionRepository(Protocol):
    async def apply_provider_outcome(
        self,
        user_id: str,
        action_id: str,
        state: ActionState,
        evidence: dict[str, Any],
        correlation_id: str,
    ) -> ActionRecord: ...

    async def resolve_action(
        self,
        *,
        action_id: str | None = None,
        provider_ref: str | None = None,
    ) -> tuple[str, ActionRecord] | None: ...


class WebhookProviderEventRepository(Protocol):
    async def record_once(self, event: ProviderEvent) -> bool: ...


class VoiceWebhookHandler:
    def __init__(
        self,
        *,
        actions: WebhookActionRepository,
        provider_events: WebhookProviderEventRepository,
        vapi: VapiVoiceAdapter,
        vapi_secret: str | None,
        twilio_verifier: TwilioVoiceCallbackVerifier | None,
        rate_limiter: WebhookRateLimiter | None = None,
    ) -> None:
        self._actions = actions
        self._provider_events = provider_events
        self._vapi = vapi
        self._vapi_secret = vapi_secret
        self._twilio_verifier = twilio_verifier
        self._rate_limiter = rate_limiter or WebhookRateLimiter()

    async def handle_vapi(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        url: str,
        *,
        correlation_id: str = "vapi-webhook",
    ) -> bool:
        self._check_size(raw_body)
        if not url.startswith(("https://", "http://localhost")):
            raise WebhookVerificationError("Vapi callbacks require a public HTTPS URL")
        try:
            self._vapi.verify_webhook(headers=headers, raw_body=raw_body, url=url)
        except (RuntimeError, ValueError) as error:
            raise WebhookVerificationError from error
        self._rate_limiter.check("vapi")
        try:
            payload = json.loads(raw_body)
        except (UnicodeDecodeError, TypeError, ValueError) as error:
            raise ValueError("Malformed Vapi callback") from error
        if not isinstance(payload, dict):
            raise ValueError("Malformed Vapi callback")

        action_id = self._metadata_value(payload, "relay_action_id")
        provider_ref = self._string_value(payload.get("callId")) or self._string_value(payload.get("call_id"))
        provider_ref = provider_ref or self._string_value((payload.get("call") or {}).get("id"))
        tool_name, outcome_payload = self._tool_outcome(payload)
        resolved = await self._actions.resolve_action(action_id=action_id, provider_ref=provider_ref)
        if resolved is None:
            raise LookupError
        user_id, action = resolved
        event_key = self._string_value(payload.get("id")) or self._string_value(payload.get("eventId"))
        if not event_key:
            raise ValueError("Vapi callback is missing an event ID")
        event = ProviderEvent(
            id=f"vapi:{event_key}",
            action_id=action.id,
            provider="vapi",
            provider_event_key=event_key,
            event_type=tool_name or self._string_value(payload.get("type")),
            provider_ref=provider_ref or action.provider_ref,
            payload_hash=sha256(raw_body).hexdigest(),
            occurred_at=datetime.now(timezone.utc),
            correlation_id=correlation_id,
        )
        if not await self._provider_events.record_once(event):
            return False
        if tool_name != "record_call_outcome" or outcome_payload is None:
            return True

        outcome = RecordCallOutcomeInput.model_validate({"action_id": action.id, **outcome_payload})
        contract_values = action.authorization_snapshot.model_dump(mode="python")
        contract_values.pop("type", None)
        contract = CallContract.model_validate({**contract_values, "action_id": action.id})
        validation = validate_call_outcome(contract, outcome)
        state = ActionState.SUCCEEDED if validation.state is ActionState.SUCCEEDED else ActionState.NEEDS_USER
        await self._actions.apply_provider_outcome(
            user_id,
            action.id,
            state,
            {"reason": validation.reason, "missing_evidence": validation.missing_evidence},
            correlation_id,
        )
        return True

    async def handle_twilio(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        url: str,
        *,
        correlation_id: str = "twilio-webhook",
    ) -> bool:
        self._check_size(raw_body)
        if self._twilio_verifier is None:
            raise WebhookVerificationError
        try:
            form = {
                key: value
                for key, value in parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True)
            }
            self._twilio_verifier.verify(headers=headers, url=url, form=form)
        except (UnicodeDecodeError, ValueError) as error:
            raise WebhookVerificationError from error
        self._rate_limiter.check("twilio")
        provider_ref = form.get("CallSid")
        status = form.get("CallStatus")
        if not provider_ref or not status:
            raise ValueError("Malformed Twilio callback")
        resolved = await self._actions.resolve_action(provider_ref=provider_ref)
        if resolved is None:
            raise LookupError
        user_id, action = resolved
        event_key = f"{provider_ref}:{status}"
        event = ProviderEvent(
            id=f"twilio:{event_key}",
            action_id=action.id,
            provider="twilio",
            provider_event_key=event_key,
            event_type=status,
            provider_ref=provider_ref,
            payload_hash=sha256(raw_body).hexdigest(),
            occurred_at=datetime.now(timezone.utc),
            correlation_id=correlation_id,
        )
        if not await self._provider_events.record_once(event):
            return False
        await self._actions.apply_provider_outcome(
            user_id,
            action.id,
            ActionState.NEEDS_USER,
            {"reason": "transport_status_without_structured_outcome", "status": status},
            correlation_id,
        )
        return True

    @staticmethod
    def _check_size(raw_body: bytes) -> None:
        if len(raw_body) > MAX_WEBHOOK_BYTES:
            raise WebhookPayloadTooLarge

    @staticmethod
    def _string_value(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @classmethod
    def _metadata_value(cls, payload: dict[str, Any], key: str) -> str | None:
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            return cls._string_value(metadata.get(key))
        return None

    @classmethod
    def _tool_outcome(cls, payload: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        tool_call = payload.get("toolCall")
        if not isinstance(tool_call, dict):
            tool_calls = payload.get("toolCalls")
            tool_call = tool_calls[0] if isinstance(tool_calls, list) and tool_calls else None
        if not isinstance(tool_call, dict):
            return None, None
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return None, None
        name = cls._string_value(function.get("name"))
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return name, None
        return name, arguments if isinstance(arguments, dict) else None


__all__ = [
    "MAX_WEBHOOK_BYTES",
    "VoiceWebhookHandler",
    "WebhookActionRepository",
    "WebhookPayloadTooLarge",
    "WebhookProviderEventRepository",
    "WebhookRateLimiter",
    "WebhookRateLimited",
    "WebhookVerificationError",
]
