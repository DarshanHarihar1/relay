from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken


class FieldCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...


class FernetFieldCipher:
    _PREFIX = "enc:v1:"

    def __init__(self, key: bytes | str):
        self._cipher = Fernet(key)

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    def encrypt(self, plaintext: str) -> str:
        return f"{self._PREFIX}{self._cipher.encrypt(plaintext.encode()).decode()}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(self._PREFIX):
            return ciphertext
        try:
            return self._cipher.decrypt(ciphertext.removeprefix(self._PREFIX).encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as error:
            raise ValueError("Cannot decrypt authenticated field") from error


_SENSITIVE_KEYS = {
    "phone_number",
    "phone",
    "booking_reference",
    "authorization",
    "access_token",
    "id_token",
    "provider_ref",
}


def redact_for_log(value: Mapping[str, Any]) -> dict[str, Any]:
    def redact(mapping: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in mapping.items():
            if key.lower() in _SENSITIVE_KEYS or key.lower().endswith(("_secret", "_key")):
                result[key] = "[REDACTED]"
            elif isinstance(item, Mapping):
                result[key] = redact(item)
            else:
                result[key] = item
        return result

    return redact(value)
