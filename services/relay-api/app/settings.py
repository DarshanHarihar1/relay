from __future__ import annotations

from dataclasses import dataclass
from os import getenv


def _required(name: str) -> str:
    value = getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing Google OAuth configuration: {name}")
    return value.strip()


@dataclass(frozen=True)
class GoogleOAuthSettings:
    """Configuration required to connect one user's Google account."""

    client_id: str
    client_secret: str
    redirect_uri: str
    gmail_label_id: str
    gmail_topic: str
    pubsub_push_audience: str
    state_signing_key: str
    state_ttl_seconds: int = 300

    def __post_init__(self) -> None:
        required = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "gmail_label_id": self.gmail_label_id,
            "gmail_topic": self.gmail_topic,
            "pubsub_push_audience": self.pubsub_push_audience,
            "state_signing_key": self.state_signing_key,
        }
        if any(not value.strip() for value in required.values()):
            raise ValueError("Google OAuth settings must not be blank")
        if not self.gmail_label_id.startswith("Label_"):
            raise ValueError("GOOGLE_GMAIL_LABEL_ID must be a Gmail label ID")
        if not self.gmail_topic.startswith("projects/"):
            raise ValueError("GOOGLE_GMAIL_TOPIC must be a fully qualified Pub/Sub topic")
        if not self.pubsub_push_audience.startswith(("https://", "http://localhost")):
            raise ValueError("GOOGLE_PUBSUB_PUSH_AUDIENCE must be an HTTPS audience")
        if not 60 <= self.state_ttl_seconds <= 600:
            raise ValueError("GOOGLE_OAUTH_STATE_TTL_SECONDS must be between 60 and 600")

    @classmethod
    def from_env(cls) -> "GoogleOAuthSettings":
        ttl = getenv("GOOGLE_OAUTH_STATE_TTL_SECONDS", "300")
        try:
            state_ttl_seconds = int(ttl)
        except ValueError as error:
            raise RuntimeError("GOOGLE_OAUTH_STATE_TTL_SECONDS must be an integer") from error
        return cls(
            client_id=_required("GOOGLE_OAUTH_CLIENT_ID"),
            client_secret=_required("GOOGLE_OAUTH_CLIENT_SECRET"),
            redirect_uri=_required("GOOGLE_OAUTH_REDIRECT_URI"),
            gmail_label_id=_required("GOOGLE_GMAIL_LABEL_ID"),
            gmail_topic=_required("GOOGLE_GMAIL_TOPIC"),
            pubsub_push_audience=_required("GOOGLE_PUBSUB_PUSH_AUDIENCE"),
            state_signing_key=_required("GOOGLE_OAUTH_STATE_SIGNING_KEY"),
            state_ttl_seconds=state_ttl_seconds,
        )
