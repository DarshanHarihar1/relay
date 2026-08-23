from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping


class TwilioVoiceCallbackVerifier:
    def __init__(self, auth_token: str | None) -> None:
        self._auth_token = auth_token

    def verify(self, *, headers: Mapping[str, str], url: str, form: Mapping[str, str]) -> None:
        if not url.startswith("https://"):
            raise ValueError("Twilio callbacks require an HTTPS URL")
        signature = next(
            (value for key, value in headers.items() if key.lower() == "x-twilio-signature"),
            None,
        )
        if not self._auth_token or not signature:
            raise ValueError("Missing Twilio callback signature")
        value = url + "".join(key + form[key] for key in sorted(form))
        expected = base64.b64encode(
            hmac.new(self._auth_token.encode(), value.encode(), hashlib.sha1).digest()
        ).decode()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid Twilio callback signature")


__all__ = ["TwilioVoiceCallbackVerifier"]
