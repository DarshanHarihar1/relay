from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from app.domain.product import (
    ActionOutcomeView,
    ApprovalActionSummary,
    ApprovalBatchView,
    DashboardView,
    OutcomeStatus,
)


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


@dataclass
class FakeVoiceAdapter:
    create_call_count: int = 0

    async def create_call(self) -> str:
        self.create_call_count += 1
        return "vapi-call-opaque"


@dataclass
class DemoSystem:
    voice: FakeVoiceAdapter = field(default_factory=FakeVoiceAdapter)
    approval_attempts: int = 0
    _approval_state: str = "awaiting_approval"
    _voice_status: OutcomeStatus = OutcomeStatus.IN_PROGRESS
    _calendar_status: OutcomeStatus = OutcomeStatus.IN_PROGRESS
    _event_keys: set[str] = field(default_factory=set)
    _approval_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def deliver_gmail_delay(self, *, message_id: str, history_id: str) -> None:
        del message_id, history_id

    async def approve_once(self, *, approval_id: str, version: int) -> bool:
        assert approval_id == "approval-demo"
        assert version == 1
        async with self._approval_lock:
            self.approval_attempts += 1
            if self._approval_state != "awaiting_approval":
                return False
            self._approval_state = "approved"
            await self.voice.create_call()
            return True

    async def approve_demo_plan_twice_concurrently(self) -> None:
        await asyncio.gather(
            self.approve_once(approval_id="approval-demo", version=1),
            self.approve_once(approval_id="approval-demo", version=1),
        )

    async def deliver_voice_outcome(self, *, action_id: str, outcome: str) -> None:
        assert action_id == "call-dinner"
        self._voice_status = {
            "CONFIRMED_PERMITTED": OutcomeStatus.VERIFIED,
            "NO_ANSWER": OutcomeStatus.NEEDS_USER,
        }[outcome]

    async def deliver_calendar_readback(
        self, *, action_id: str, exists: bool, visibility: str
    ) -> None:
        assert action_id == "calendar-hold"
        self._calendar_status = (
            OutcomeStatus.VERIFIED
            if exists and visibility == "private"
            else OutcomeStatus.NEEDS_USER
        )

    async def redeliver_voice_webhook(self, *, provider_event_id: str) -> None:
        if provider_event_id in self._event_keys:
            return
        self._event_keys.add(provider_event_id)
        await self.deliver_voice_outcome(action_id="call-dinner", outcome="NO_ANSWER")

    async def dashboard(self, *, user_id: str) -> DashboardView:
        assert user_id == "demo"
        return DashboardView(
            repair_plan_id="plan-demo",
            repair_plan_version=1,
            generated_at=NOW,
            timeline=(),
            approval=ApprovalBatchView(
                approval_id="approval-demo",
                version=1,
                state=self._approval_state,
                expires_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
                reason="Review these limited actions.",
                actions=(
                    ApprovalActionSummary(
                        action_id="call-dinner",
                        kind="voice_call",
                        goal="Confirm the limited dinner timing",
                        authorized_options=("confirm_new_time",),
                        max_fee_inr=0,
                        expires_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
                        disclosure="Relay will identify itself.",
                        must_not=("make payment",),
                    ),
                    ApprovalActionSummary(
                        action_id="calendar-hold",
                        kind="calendar_hold",
                        goal="Private Calendar hold",
                        authorized_options=(),
                        max_fee_inr=0,
                        expires_at=None,
                        disclosure=None,
                        must_not=("change event visibility",),
                    ),
                    ApprovalActionSummary(
                        action_id="uber-trip",
                        kind="uber_deep_link",
                        goal="Open Uber with this trip",
                        authorized_options=(),
                        max_fee_inr=0,
                        expires_at=None,
                        disclosure=None,
                        must_not=("book or pay for a ride",),
                    ),
                ),
            ),
            outcomes=(
                ActionOutcomeView(
                    action_id="call-dinner",
                    kind="voice_call",
                    status=self._voice_status,
                    summary="Verified" if self._voice_status is OutcomeStatus.VERIFIED else "Needs your attention",
                    occurred_at=NOW,
                ),
                ActionOutcomeView(
                    action_id="calendar-hold",
                    kind="calendar_hold",
                    status=self._calendar_status,
                    summary="Verified in your calendar"
                    if self._calendar_status is OutcomeStatus.VERIFIED
                    else "Calendar update sent, awaiting verification",
                    occurred_at=NOW,
                ),
                ActionOutcomeView(
                    action_id="uber-trip",
                    kind="uber_deep_link",
                    status=OutcomeStatus.HANDOFF,
                    summary="Uber opened. Confirm fare and booking in Uber",
                    occurred_at=NOW,
                ),
            ),
            last_event_id=None,
        )


@pytest.fixture
def system() -> DemoSystem:
    return DemoSystem()
