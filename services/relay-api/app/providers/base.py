from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from app.contracts import CallContract, RecordCallOutcomeInput
from app.services.retry_policy import ProviderFailure


class ProviderCallRef(Protocol):
    provider_ref: str


class VoiceAdapter(Protocol):
    async def create_call(self, contract: CallContract, *, idempotency_key: str) -> ProviderCallRef: ...

    async def get_final_outcome(self, provider_ref: str) -> RecordCallOutcomeInput | None: ...

    def verify_webhook(self, *, headers: Mapping[str, str], raw_body: bytes, url: str) -> None: ...


__all__ = ["ProviderCallRef", "ProviderFailure", "VoiceAdapter"]
