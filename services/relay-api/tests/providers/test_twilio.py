from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.providers.twilio import TwilioVoiceCallbackVerifier


def test_twilio_signature_verifier_accepts_the_exact_callback_url() -> None:
    url = "https://relay.example/v1/webhooks/twilio"
    form = {"CallSid": "CA1", "CallStatus": "completed"}
    token = "twilio-auth-token"
    value = url + "".join(key + form[key] for key in sorted(form))
    signature = base64.b64encode(hmac.new(token.encode(), value.encode(), hashlib.sha1).digest()).decode()

    TwilioVoiceCallbackVerifier(token).verify(
        headers={"X-Twilio-Signature": signature},
        url=url,
        form=form,
    )


def test_twilio_signature_verifier_rejects_a_non_https_production_url() -> None:
    with pytest.raises(ValueError):
        TwilioVoiceCallbackVerifier("twilio-auth-token").verify(
            headers={"X-Twilio-Signature": "bad"},
            url="http://relay.example/v1/webhooks/twilio",
            form={},
        )
