from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.contracts import CallContract, JsonValue, RecordCallOutcomeInput
from app.services.retry_policy import ProviderFailure


@dataclass(frozen=True)
class VoiceDestination:
    number: str
    consented_for_voice: bool


@dataclass(frozen=True)
class VapiCallRef:
    provider_ref: str


def record_call_outcome_tool() -> dict[str, JsonValue]:
    return {
        "type": "function",
        "function": {
            "name": "record_call_outcome",
            "description": "Record only the bounded result of the authorized call.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["outcome", "requested_transfer"],
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": [
                            "confirmed",
                            "declined",
                            "no_answer",
                            "voicemail",
                            "transfer_requested",
                            "contradiction",
                            "unexpected_fee",
                            "provider_error",
                        ],
                    },
                    "venue": {"type": ["string", "null"]},
                    "date": {"type": ["string", "null"], "format": "date"},
                    "party_size": {"type": ["integer", "null"], "minimum": 1, "maximum": 20},
                    "confirmed_time": {"type": ["string", "null"], "format": "time"},
                    "fee_inr": {"type": ["number", "null"], "minimum": 0},
                    "requested_transfer": {"type": "boolean"},
                    "redacted_excerpt": {"type": ["string", "null"], "maxLength": 280},
                },
            },
        },
    }


def bounded_call_prompt(contract: CallContract) -> str:
    options = ", ".join(sorted(contract.authorized_options))
    prohibition_labels = {
        "make_payment": "transact",
        "share_sensitive_data": "share sensitive data",
        "accept_unlisted_time": "accept an unlisted time",
    }
    prohibitions = ", ".join(
        prohibition_labels.get(value, value.replace("_", " ")) for value in sorted(contract.must_not)
    )
    evidence = ", ".join(sorted(contract.required_evidence))
    return (
        f"Goal: {contract.goal}\n"
        f"Allowed options: {options}\n"
        f"Identity: {contract.identity_disclosure}\n"
        f"Do not {prohibitions}.\n"
        "Do not invent or accept an option outside the allowed options. "
        "Do not transfer the call to a person. "
        f"Before ending, make exactly one record_call_outcome tool call with evidence: {evidence}."
    )


def make_assistant(contract: CallContract, *, server_url: str) -> dict[str, JsonValue]:
    return {
        "firstMessage": contract.identity_disclosure,
        "model": {
            "provider": "google",
            "model": "gemini-2.5-flash",
            "tools": [record_call_outcome_tool()],
        },
        "serverUrl": server_url,
        "serverMessages": ["tool-calls", "end-of-call-report"],
        "systemPrompt": bounded_call_prompt(contract),
    }


class VapiVoiceAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        destination_resolver: Callable[[str], VoiceDestination] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
        base_url: str = "https://api.vapi.ai",
    ) -> None:
        self._settings = settings
        self._destination_resolver = destination_resolver or (
            lambda recipient_ref: VoiceDestination(recipient_ref, consented_for_voice=False)
        )
        self._transport = transport
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    async def create_call(self, contract: CallContract, *, idempotency_key: str) -> VapiCallRef:
        if not self._settings.vapi_private_key:
            raise RuntimeError("VAPI_PRIVATE_KEY is required for voice execution")
        destination = self._destination_resolver(contract.recipient_ref)
        if not destination.consented_for_voice:
            raise ProviderFailure("Voice destination is not consented", validation_failure=True)
        server_url = self._settings.vapi_server_url or (
            f"{self._settings.relay_public_base_url.rstrip('/')}/v1/webhooks/vapi"
            if self._settings.relay_public_base_url
            else ""
        )
        if not server_url:
            raise RuntimeError("RELAY_PUBLIC_BASE_URL or VAPI_SERVER_URL is required for voice execution")
        body = {
            "phoneNumberId": self._settings.twilio_phone_number,
            "customer": {"number": destination.number},
            "assistant": make_assistant(contract, server_url=server_url),
            "metadata": {
                "relay_action_id": contract.action_id,
                "relay_idempotency_key": idempotency_key,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    f"{self._base_url}/call",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._settings.vapi_private_key}",
                        "Content-Type": "application/json",
                        "Idempotency-Key": idempotency_key,
                    },
                )
        except httpx.TimeoutException as error:
            raise ProviderFailure("Vapi call creation timed out", timed_out=True) from error
        except httpx.NetworkError as error:
            raise ProviderFailure("Vapi call creation failed on the network", timed_out=True) from error

        if response.status_code == 408 or response.status_code == 429 or response.status_code >= 500:
            raise ProviderFailure(
                f"Vapi call creation failed: {response.status_code}",
                status_code=response.status_code,
            )
        if response.status_code == 401 or response.status_code == 403:
            raise ProviderFailure(
                f"Vapi authorization failed: {response.status_code}",
                status_code=response.status_code,
                authorization_failure=True,
            )
        if response.status_code >= 400:
            raise ProviderFailure(
                f"Vapi rejected the call: {response.status_code}",
                status_code=response.status_code,
                validation_failure=True,
            )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise ProviderFailure("Vapi returned an unreadable call reference", unknown_outcome=True) from error
        provider_ref = payload.get("id") or payload.get("callId") or payload.get("call_id")
        if not isinstance(provider_ref, str) or not provider_ref:
            raise ProviderFailure("Vapi response omitted the call reference", unknown_outcome=True)
        return VapiCallRef(provider_ref=provider_ref)

    async def get_final_outcome(self, provider_ref: str) -> RecordCallOutcomeInput | None:
        del provider_ref
        return None

    def verify_webhook(self, *, headers: Mapping[str, str], raw_body: bytes, url: str) -> None:
        del headers, raw_body, url
        raise NotImplementedError


__all__ = [
    "VapiCallRef",
    "VapiVoiceAdapter",
    "VoiceDestination",
    "bounded_call_prompt",
    "make_assistant",
    "record_call_outcome_tool",
]
