from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.contracts import ActionRecord, ActionState
from app.providers.vapi import VapiVoiceAdapter, VoiceDestination
from app.providers.webhooks import VoiceWebhookHandler


NOW = datetime(2027, 8, 23, 12, 0, tzinfo=timezone.utc)


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


class FakeActionRepository:
    def __init__(self) -> None:
        self.action = ActionRecord(
            id="action-1",
            user_id="user-1",
            repair_plan_id="plan-1",
            repair_plan_version=1,
            type="voice_call",
            target_ref="place:toscano",
            idempotency_key="relay-action-v1:stable",
            authorization_snapshot={
                "type": "voice_call",
                "goal": "Confirm the reservation",
                "recipient_ref": "place:toscano",
                "identity_disclosure": "I am Relay, an assistant calling on Darshan's behalf.",
                "authorized_options": ["23:15"],
                "max_fee_inr": 0,
                "must_not": ["make_payment"],
                "required_evidence": ["venue", "date", "party_size", "confirmed_time"],
                "expires_at": NOW + timedelta(hours=1),
            },
            provider_ref="call-1",
            state=ActionState.IN_PROGRESS,
            expires_at=NOW + timedelta(hours=1),
            correlation_id="corr-1",
        )
        self.updated: list[ActionState] = []

    async def get(self, user_id: str, action_id: str) -> ActionRecord | None:
        if user_id == self.action.user_id and action_id == self.action.id:
            return self.action
        return None

    async def resolve_action(self, *, action_id=None, provider_ref=None):
        if (action_id == self.action.id or action_id is None) and (
            provider_ref is None or provider_ref == self.action.provider_ref
        ):
            return self.action.user_id, self.action
        return None

    async def apply_provider_outcome(self, user_id, action_id, state, evidence, correlation_id):
        del user_id, action_id, evidence, correlation_id
        self.action = self.action.model_copy(update={"state": state})
        self.updated.append(state)
        return self.action


class FakeProviderEvents:
    def __init__(self) -> None:
        self.events: set[str] = set()

    async def record_once(self, event):
        if event.provider_event_key in self.events:
            return False
        self.events.add(event.provider_event_key)
        return True


def vapi_body() -> bytes:
    return json.dumps(
        {
            "id": "event-1",
            "type": "tool-calls",
            "metadata": {"relay_action_id": "action-1"},
            "toolCall": {
                "function": {
                    "name": "record_call_outcome",
                    "arguments": json.dumps(
                        {
                            "outcome": "confirmed",
                            "venue": "Toscano",
                            "date": "2027-08-23",
                            "party_size": 2,
                            "confirmed_time": "23:15:00",
                            "requested_transfer": False,
                        }
                    ),
                }
            },
        },
        separators=(",", ":"),
    ).encode()


@pytest.mark.asyncio
async def test_vapi_callback_is_replayed_without_a_second_action_update() -> None:
    actions = FakeActionRepository()
    handler = VoiceWebhookHandler(
        actions=actions,
        provider_events=FakeProviderEvents(),
        vapi=VapiVoiceAdapter(settings(), destination_resolver=lambda _: VoiceDestination("+1", True)),
        vapi_secret="vapi-webhook-secret",
        twilio_verifier=None,
    )
    raw_body = vapi_body()
    signature = hmac.new(b"vapi-webhook-secret", raw_body, hashlib.sha256).hexdigest()

    await handler.handle_vapi(raw_body, {"x-vapi-signature": signature}, "https://relay.example/v1/webhooks/vapi")
    await handler.handle_vapi(raw_body, {"x-vapi-signature": signature}, "https://relay.example/v1/webhooks/vapi")

    assert actions.updated == [ActionState.SUCCEEDED]


def test_invalid_vapi_signature_is_rejected_before_route_work() -> None:
    handler = VoiceWebhookHandler(
        actions=FakeActionRepository(),
        provider_events=FakeProviderEvents(),
        vapi=VapiVoiceAdapter(settings(), destination_resolver=lambda _: VoiceDestination("+1", True)),
        vapi_secret="vapi-webhook-secret",
        twilio_verifier=None,
    )
    with pytest.raises(Exception):
        import asyncio

        asyncio.run(handler.handle_vapi(b"{}", {"x-vapi-signature": "bad"}, "https://relay.example/v1/webhooks/vapi"))
