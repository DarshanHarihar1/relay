from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import Settings
from app.contracts import CallContract
from app.providers.vapi import VapiVoiceAdapter, VoiceDestination
from app.services.retry_policy import ProviderFailure


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def settings() -> Settings:
    return Settings(
        google_cloud_project=None,
        firebase_project_id=None,
        google_oauth_client_id=None,
        google_oauth_redirect_uri=None,
        google_oauth_client_secret_name=None,
        maps_api_key_secret_name=None,
        app_encryption_key_secret_name=None,
        vapi_private_key="vapi-private-key",
        vapi_webhook_secret="vapi-webhook-secret",
        twilio_account_sid="twilio-sid",
        twilio_auth_token="twilio-auth-token",
        twilio_phone_number="+15550001111",
        relay_public_base_url="https://relay.example",
        vapi_server_url="https://relay.example/v1/webhooks/vapi",
        app_encryption_key="encrypted-key",
    )


def call_contract() -> CallContract:
    return CallContract(
        action_id="action-1",
        goal="Confirm the restaurant reservation",
        recipient_ref="place:toscano",
        identity_disclosure="I am Relay, an assistant calling on Darshan's behalf.",
        authorized_options=["23:15"],
        max_fee_inr=0,
        must_not={"make_payment", "share_sensitive_data", "accept_unlisted_time"},
        required_evidence={"venue", "date", "party_size", "confirmed_time"},
        expires_at=NOW + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_vapi_request_has_one_outcome_tool_and_no_payment_tool() -> None:
    requests: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "call-1"}, request=request)

    adapter = VapiVoiceAdapter(
        settings(),
        destination_resolver=lambda _: VoiceDestination("+15550002222", consented_for_voice=True),
        transport=httpx.MockTransport(transport),
    )
    result = await adapter.create_call(call_contract(), idempotency_key="stable-key")

    body = json.loads(requests[0].content)
    tools = body["assistant"]["model"]["tools"]
    assert [tool["function"]["name"] for tool in tools] == ["record_call_outcome"]
    assert "payment" not in json.dumps(body).lower()
    assert call_contract().identity_disclosure in body["assistant"]["firstMessage"]
    assert body["metadata"] == {"relay_action_id": "action-1", "relay_idempotency_key": "stable-key"}
    assert result.provider_ref == "call-1"


@pytest.mark.asyncio
async def test_vapi_timeout_is_a_transient_provider_failure() -> None:
    async def transport(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    adapter = VapiVoiceAdapter(
        settings(),
        destination_resolver=lambda _: VoiceDestination("+15550002222", consented_for_voice=True),
        transport=httpx.MockTransport(transport),
    )

    with pytest.raises(ProviderFailure) as raised:
        await adapter.create_call(call_contract(), idempotency_key="stable-key")

    assert raised.value.timed_out is True


@pytest.mark.asyncio
async def test_vapi_rejects_a_destination_without_voice_consent() -> None:
    adapter = VapiVoiceAdapter(
        settings(),
        destination_resolver=lambda _: VoiceDestination("+15550002222", consented_for_voice=False),
    )

    with pytest.raises(ProviderFailure) as raised:
        await adapter.create_call(call_contract(), idempotency_key="stable-key")

    assert raised.value.validation_failure is True


def test_execution_configuration_requires_provider_values(monkeypatch) -> None:
    for name in (
        "VAPI_PRIVATE_KEY",
        "VAPI_WEBHOOK_SECRET",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "RELAY_PUBLIC_BASE_URL",
        "APP_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="VAPI_PRIVATE_KEY"):
        Settings.from_env().require_execution_configuration()
