from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Settings:
    google_cloud_project: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(google_cloud_project=getenv("GOOGLE_CLOUD_PROJECT") or None)

    def require_cloud_configuration(self) -> "Settings":
        if self.google_cloud_project is None:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for deployed startup")
        return self
