from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Literal, Protocol

from app.security import FieldCipher


NotificationKind = Literal["approval_needed", "outcome_updated"]


class NotificationSendError(Exception):
    """A provider failure carrying the stable provider error code only."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class DeviceRepository(Protocol):
    async def upsert_device(
        self,
        *,
        user_id: str,
        token_fingerprint: str,
        encrypted_token: str,
        platform: str,
        last_seen_at: datetime,
    ) -> None: ...

    async def list_devices(self, user_id: str) -> list[Mapping[str, object]]: ...

    async def remove_device(self, *, user_id: str, token_fingerprint: str) -> None: ...


class NotificationPort(Protocol):
    async def send(self, *, token: str, payload: dict[str, dict[str, str]]) -> None: ...


class FirebaseNotificationPort:
    """Sends data-only messages through the Firebase Admin SDK."""

    async def send(self, *, token: str, payload: dict[str, dict[str, str]]) -> None:
        try:
            from firebase_admin import messaging

            message = messaging.Message(token=token, data=payload["data"])
            await asyncio.to_thread(messaging.send, message)
        except Exception as error:  # noqa: BLE001 - normalize provider failures at this boundary
            code = getattr(error, "code", None)
            if not isinstance(code, str):
                code = "firebase_send_failed"
            raise NotificationSendError(
                code,
                retryable=code in {"messaging/server-unavailable", "messaging/internal-error"},
            ) from error


class NotificationService:
    _MAX_SEND_ATTEMPTS = 3
    _INVALID_TOKEN_CODES = frozenset(
        {
            "messaging/registration-token-not-registered",
            "messaging/invalid-registration-token",
        }
    )

    def __init__(
        self, *, repository: DeviceRepository, cipher: FieldCipher, sender: NotificationPort
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._sender = sender

    async def register_device(
        self,
        *,
        user_id: str,
        token: str,
        platform: Literal["web"] = "web",
        now: datetime | None = None,
    ) -> None:
        if not token or not user_id:
            raise ValueError("A user and device token are required")
        encrypted_token = self._cipher.encrypt(token)
        token_fingerprint = hashlib.sha256(token.encode()).hexdigest()
        timestamp = now or datetime.now(timezone.utc)
        await self._repository.upsert_device(
            user_id=user_id,
            token_fingerprint=token_fingerprint,
            encrypted_token=encrypted_token,
            platform=platform,
            last_seen_at=timestamp,
        )

    async def notify_dashboard_change(
        self,
        *,
        user_id: str,
        kind: NotificationKind,
        entity_id: str,
        correlation_id: str,
    ) -> int:
        if not user_id or not entity_id or not correlation_id:
            raise ValueError("Notification identifiers are required")
        payload = {
            "data": {
                "kind": kind,
                "entity_id": entity_id,
                "correlation_id": correlation_id,
            }
        }
        delivered = 0
        for device in await self._repository.list_devices(user_id):
            encrypted_token = device.get("encrypted_token")
            token_fingerprint = device.get("token_fingerprint")
            if not isinstance(encrypted_token, str) or not isinstance(token_fingerprint, str):
                continue
            try:
                token = self._cipher.decrypt(encrypted_token)
            except ValueError:
                await self._repository.remove_device(
                    user_id=user_id, token_fingerprint=token_fingerprint
                )
                continue
            for attempt in range(self._MAX_SEND_ATTEMPTS):
                try:
                    await self._sender.send(token=token, payload=payload)
                except NotificationSendError as error:
                    if error.code in self._INVALID_TOKEN_CODES:
                        await self._repository.remove_device(
                            user_id=user_id, token_fingerprint=token_fingerprint
                        )
                        break
                    if not error.retryable or attempt == self._MAX_SEND_ATTEMPTS - 1:
                        break
                else:
                    delivered += 1
                    break
        return delivered


__all__ = [
    "DeviceRepository",
    "FirebaseNotificationPort",
    "NotificationKind",
    "NotificationPort",
    "NotificationSendError",
    "NotificationService",
]
