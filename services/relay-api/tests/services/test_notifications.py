from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from app.security import FernetFieldCipher
from app.services.notifications import NotificationService, NotificationSendError


@dataclass
class FakeDeviceRepository:
    devices: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    removed_tokens: int = 0

    async def upsert_device(self, **device: str) -> None:
        self.devices[(device["user_id"], device["token_fingerprint"])] = device

    async def list_devices(self, user_id: str) -> list[dict[str, str]]:
        return [device for (owner, _), device in self.devices.items() if owner == user_id]

    async def remove_device(self, *, user_id: str, token_fingerprint: str) -> None:
        self.devices.pop((user_id, token_fingerprint), None)
        self.removed_tokens += 1


@dataclass
class FakeNotificationPort:
    payloads: list[dict[str, dict[str, str]]] = field(default_factory=list)
    raise_code: str | None = None

    async def send(self, *, token: str, payload: dict[str, dict[str, str]]) -> None:
        if self.raise_code is not None:
            raise NotificationSendError(self.raise_code)
        self.payloads.append(payload)


def _service(repo: FakeDeviceRepository, port: FakeNotificationPort) -> NotificationService:
    return NotificationService(repository=repo, cipher=FernetFieldCipher(FernetFieldCipher.generate_key()), sender=port)


@pytest.mark.asyncio
async def test_notification_payload_is_data_only_and_uses_opaque_ids() -> None:
    repo = FakeDeviceRepository()
    port = FakeNotificationPort()
    service = _service(repo, port)
    await service.register_device(
        user_id="u1",
        token="f" * 32,
        platform="web",
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    await service.notify_dashboard_change(
        user_id="u1",
        kind="approval_needed",
        entity_id="approval_1",
        correlation_id="corr_1",
    )

    assert port.payloads == [
        {"data": {"kind": "approval_needed", "entity_id": "approval_1", "correlation_id": "corr_1"}}
    ]


@pytest.mark.asyncio
async def test_bad_fcm_token_is_removed_without_retry_loop() -> None:
    repo = FakeDeviceRepository()
    port = FakeNotificationPort(raise_code="messaging/registration-token-not-registered")
    service = _service(repo, port)
    await service.register_device(user_id="u1", token="f" * 32, platform="web")

    await service.notify_dashboard_change(
        user_id="u1",
        kind="outcome_updated",
        entity_id="act_1",
        correlation_id="corr_1",
    )

    assert repo.removed_tokens == 1
    assert repo.devices == {}


@pytest.mark.asyncio
async def test_token_is_stored_encrypted_and_registration_is_idempotent() -> None:
    repo = FakeDeviceRepository()
    port = FakeNotificationPort()
    service = _service(repo, port)

    await service.register_device(user_id="u1", token="f" * 32, platform="web")
    await service.register_device(user_id="u1", token="f" * 32, platform="web")

    assert len(repo.devices) == 1
    stored = next(iter(repo.devices.values()))
    assert stored["encrypted_token"].startswith("enc:v1:")
    assert stored["encrypted_token"] != "f" * 32
