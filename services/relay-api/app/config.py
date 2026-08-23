from dataclasses import dataclass
from os import getenv


def _configured(name: str) -> str | None:
    return getenv(name) or None


@dataclass(frozen=True)
class Settings:
    google_cloud_project: str | None
    firebase_project_id: str | None
    google_oauth_client_id: str | None
    google_oauth_redirect_uri: str | None
    google_oauth_client_secret_name: str | None
    maps_api_key_secret_name: str | None
    app_encryption_key_secret_name: str | None
    vapi_private_key: str | None = None
    vapi_webhook_secret: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None
    relay_public_base_url: str | None = None
    vapi_server_url: str | None = None
    app_encryption_key: str | None = None
    uber_client_id: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            google_cloud_project=_configured("GOOGLE_CLOUD_PROJECT"),
            firebase_project_id=_configured("FIREBASE_PROJECT_ID"),
            google_oauth_client_id=_configured("GOOGLE_OAUTH_CLIENT_ID"),
            google_oauth_redirect_uri=_configured("GOOGLE_OAUTH_REDIRECT_URI"),
            google_oauth_client_secret_name=_configured("GOOGLE_OAUTH_CLIENT_SECRET_NAME"),
            maps_api_key_secret_name=_configured("MAPS_API_KEY_SECRET_NAME"),
            app_encryption_key_secret_name=_configured("APP_ENCRYPTION_KEY_SECRET_NAME"),
            vapi_private_key=_configured("VAPI_PRIVATE_KEY"),
            vapi_webhook_secret=_configured("VAPI_WEBHOOK_SECRET"),
            twilio_account_sid=_configured("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_configured("TWILIO_AUTH_TOKEN"),
            twilio_phone_number=_configured("TWILIO_PHONE_NUMBER"),
            relay_public_base_url=_configured("RELAY_PUBLIC_BASE_URL"),
            vapi_server_url=_configured("VAPI_SERVER_URL"),
            app_encryption_key=_configured("APP_ENCRYPTION_KEY"),
            uber_client_id=_configured("UBER_CLIENT_ID"),
        )

    def require_cloud_configuration(self) -> "Settings":
        required = {
            "GOOGLE_CLOUD_PROJECT": self.google_cloud_project,
            "FIREBASE_PROJECT_ID": self.firebase_project_id,
            "GOOGLE_OAUTH_CLIENT_ID": self.google_oauth_client_id,
            "GOOGLE_OAUTH_REDIRECT_URI": self.google_oauth_redirect_uri,
            "GOOGLE_OAUTH_CLIENT_SECRET_NAME": self.google_oauth_client_secret_name,
            "MAPS_API_KEY_SECRET_NAME": self.maps_api_key_secret_name,
            "APP_ENCRYPTION_KEY_SECRET_NAME": self.app_encryption_key_secret_name,
        }
        missing = ", ".join(name for name, value in required.items() if value is None)
        if missing:
            raise RuntimeError(f"Missing deployed configuration: {missing}")
        return self

    def require_execution_configuration(self, *, local: bool = False) -> "Settings":
        required = {
            "VAPI_PRIVATE_KEY": self.vapi_private_key,
            "VAPI_WEBHOOK_SECRET": self.vapi_webhook_secret,
            "TWILIO_ACCOUNT_SID": self.twilio_account_sid,
            "TWILIO_AUTH_TOKEN": self.twilio_auth_token,
            "TWILIO_PHONE_NUMBER": self.twilio_phone_number,
            "RELAY_PUBLIC_BASE_URL": self.relay_public_base_url,
            "APP_ENCRYPTION_KEY": self.app_encryption_key,
        }
        missing = ", ".join(name for name, value in required.items() if not value)
        if missing:
            raise RuntimeError(f"Missing execution configuration: {missing}")
        if not local and not self.relay_public_base_url.startswith("https://"):
            raise RuntimeError("RELAY_PUBLIC_BASE_URL must use HTTPS outside local tests")
        return self
