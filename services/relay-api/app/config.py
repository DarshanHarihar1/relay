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
