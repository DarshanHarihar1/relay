from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = REPOSITORY_ROOT / "services" / "relay-api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.contracts import (  # noqa: E402
    ActionRecord,
    ActionState,
    Approval,
    CalendarHoldAuthorizationSnapshot,
    Commitment,
    UberDeepLinkAuthorizationSnapshot,
    VoiceCallAuthorizationSnapshot,
)
from app.domain.impact import CandidateChange, CandidateKind, RepairCandidate, RepairPlan  # noqa: E402
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document  # noqa: E402
from app.services.action_state import derive_action_idempotency_key  # noqa: E402


SeedMode = Literal["rehearsal", "live"]


class DemoSeedSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    commitment_ids: tuple[str, str, str, str]
    repair_plan_id: str
    approval_id: str
    seeded_at: datetime
    mode: SeedMode


@dataclass(frozen=True)
class DemoSeedRecords:
    summary: DemoSeedSummary
    commitments: tuple[Commitment, ...]
    plan: RepairPlan
    approval: Approval
    actions: tuple[ActionRecord, ...]


class DemoSeedRepository(Protocol):
    async def get_seed(self, *, user_id: str) -> DemoSeedSummary | None: ...

    async def save_seed(self, records: DemoSeedRecords) -> None: ...


@dataclass
class InMemoryDemoRepository:
    seeds: dict[str, DemoSeedSummary] = field(default_factory=dict)
    commitments: dict[str, tuple[Commitment, ...]] = field(default_factory=dict)
    plans: dict[str, RepairPlan] = field(default_factory=dict)

    async def get_seed(self, *, user_id: str) -> DemoSeedSummary | None:
        return self.seeds.get(user_id)

    async def save_seed(self, records: DemoSeedRecords) -> None:
        if records.summary.user_id in self.seeds:
            return
        self.seeds[records.summary.user_id] = records.summary
        self.commitments[records.summary.user_id] = records.commitments
        self.plans[records.plan.id] = records.plan

    async def count_commitments(self, *, user_id: str) -> int:
        return len(self.commitments.get(user_id, ()))

    async def get_plan(self, plan_id: str) -> RepairPlan:
        return self.plans[plan_id]


class FirestoreDemoRepository:
    def __init__(self, client) -> None:
        self._client = client

    async def get_seed(self, *, user_id: str) -> DemoSeedSummary | None:
        snapshot = await self._client.document(user_document(user_id, "demo_seed", "current")).get()
        if not snapshot.exists:
            return None
        return DemoSeedSummary.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def save_seed(self, records: DemoSeedRecords) -> None:
        seed_ref = self._client.document(user_document(records.summary.user_id, "demo_seed", "current"))
        if (await seed_ref.get()).exists:
            return
        batch = self._client.batch()
        user_id = records.summary.user_id
        for commitment in records.commitments:
            batch.set(
                self._client.document(user_document(user_id, "commitments", commitment.id)),
                firestore_data(commitment),
            )
        batch.set(
            self._client.document(user_document(user_id, "repair_plans", records.plan.id)),
            firestore_data(records.plan),
        )
        batch.set(
            self._client.document(user_document(user_id, "approvals", records.approval.id)),
            firestore_data(records.approval),
        )
        for action in records.actions:
            batch.set(
                self._client.document(user_document(user_id, "actions", action.id)),
                firestore_data(action),
            )
        batch.create(seed_ref, records.summary.model_dump(mode="python"))
        await batch.commit()


class DemoSeeder:
    def __init__(self, repo: DemoSeedRepository) -> None:
        self.repo = repo

    async def seed_demo_data(
        self, *, user_id: str, now: datetime, mode: SeedMode
    ) -> DemoSeedSummary:
        _validate_seed_input(user_id=user_id, now=now, mode=mode)
        if mode == "live":
            _require_live_readiness()
        existing = await self.repo.get_seed(user_id=user_id)
        if existing is not None:
            return existing
        records = _build_records(user_id=user_id, now=now, mode=mode)
        await self.repo.save_seed(records)
        return records.summary


def _build_records(*, user_id: str, now: datetime, mode: SeedMode) -> DemoSeedRecords:
    flight_id, pickup_id, dinner_id, hotel_id = (
        "flight_arrival",
        "pickup_prompt",
        "dinner_reservation",
        "hotel_checkin",
    )
    flight_start = now.replace(hour=20, minute=0, second=0, microsecond=0)
    flight_end = now.replace(hour=22, minute=5, second=0, microsecond=0)
    commitments = (
        _commitment(user_id, flight_id, "Flight arrival at 22:05", flight_start, flight_end, "flight"),
        _commitment(user_id, pickup_id, "Optional pickup from the airport", flight_end, flight_end + timedelta(minutes=25), "pickup"),
        _commitment(user_id, dinner_id, "Dinner reservation at 22:00", now.replace(hour=22, minute=0, second=0, microsecond=0), now.replace(hour=23, minute=0, second=0, microsecond=0), "dinner"),
        _commitment(user_id, hotel_id, "Hotel check-in", now.replace(hour=23, minute=30, second=0, microsecond=0), now.replace(hour=23, minute=45, second=0, microsecond=0), "hotel"),
    )
    plan_id = "plan_demo_delay_v1"
    approval_id = "approval_demo_delay_v1"
    candidate_id = "candidate_demo_delay_v1"
    plan = RepairPlan(
        id=plan_id,
        version=1,
        assessment_id="assessment_demo_delay_v1",
        selected_candidate_id=candidate_id,
        candidates=(
            RepairCandidate(
                id=candidate_id,
                kind=CandidateKind.RESCHEDULE,
                changes=(
                    CandidateChange(
                        commitment_id=dinner_id,
                        kind=CandidateKind.RESCHEDULE,
                        proposed_start=now.replace(hour=22, minute=30, second=0, microsecond=0),
                        proposed_end=now.replace(hour=23, minute=30, second=0, microsecond=0),
                        action_kinds=(),
                    ),
                ),
                invalid_reasons=(),
                explanation="Protect the arrival, pickup choice, dinner, and hotel timeline.",
            ),
        ),
        approval_id=approval_id,
        input_fingerprint="demo-delay-input-v1",
    )
    expires_at = now + timedelta(hours=2)
    actions = _actions(user_id=user_id, plan_id=plan_id, plan_version=1, now=now, expires_at=expires_at)
    approval = Approval(
        id=approval_id,
        user_id=user_id,
        action_ids=[action.id for action in actions],
        state="awaiting_approval",
        version=1,
        correlation_id="demo:approval",
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
    )
    summary = DemoSeedSummary(
        user_id=user_id,
        commitment_ids=(flight_id, pickup_id, dinner_id, hotel_id),
        repair_plan_id=plan_id,
        approval_id=approval_id,
        seeded_at=now,
        mode=mode,
    )
    return DemoSeedRecords(summary, commitments, plan, approval, actions)


def _commitment(user_id: str, commitment_id: str, summary: str, start: datetime, end: datetime, kind: str) -> Commitment:
    return Commitment(
        id=commitment_id,
        user_id=user_id,
        source_event_key=f"demo:{commitment_id}",
        summary=summary,
        starts_at=start,
        ends_at=end,
        created_at=start - timedelta(hours=1),
        updated_at=start - timedelta(hours=1),
        correlation_id=f"demo:{commitment_id}",
        type=kind,
        criticality="HIGH" if kind in {"flight", "hotel"} else "NORMAL",
        flexibility="FIXED" if kind == "flight" else "NEGOTIABLE",
        protected=False,
    )


def _actions(*, user_id: str, plan_id: str, plan_version: int, now: datetime, expires_at: datetime) -> tuple[ActionRecord, ...]:
    snapshots = (
        (
            "action_demo_call",
            "voice_call",
            "demo:pre-consented-recipient",
            VoiceCallAuthorizationSnapshot(
                type="voice_call",
                goal="Confirm the limited dinner timing",
                recipient_ref="demo:pre-consented-recipient",
                identity_disclosure="Relay will identify itself.",
                authorized_options=["22:30"],
                max_fee_inr=0,
                must_not=["make payment", "transfer the call"],
                required_evidence=["venue", "date", "confirmed_time"],
                expires_at=expires_at,
            ),
        ),
        (
            "action_demo_calendar",
            "calendar_hold",
            "primary",
            CalendarHoldAuthorizationSnapshot(
                type="calendar_hold",
                calendar_id="primary",
                start_at=now.replace(hour=22, minute=5, second=0, microsecond=0),
                end_at=now.replace(hour=22, minute=30, second=0, microsecond=0),
                visibility="private",
            ),
        ),
        (
            "action_demo_uber",
            "uber_deep_link",
            "demo:airport-to-hotel",
            UberDeepLinkAuthorizationSnapshot(
                type="uber_deep_link",
                pickup="demo:airport",
                destination="demo:hotel",
                handoff_label="Open Uber",
            ),
        ),
    )
    return tuple(
        ActionRecord(
            id=action_id,
            user_id=user_id,
            repair_plan_id=plan_id,
            repair_plan_version=plan_version,
            type=action_type,
            target_ref=target_ref,
            idempotency_key=derive_action_idempotency_key(plan_version, action_type, target_ref, snapshot),
            authorization_snapshot=snapshot,
            state=ActionState.AWAITING_APPROVAL,
            correlation_id="demo:plan",
            expires_at=expires_at if action_type == "voice_call" else None,
            created_at=now,
            updated_at=now,
        )
        for action_id, action_type, target_ref, snapshot in snapshots
    )


def _validate_seed_input(*, user_id: str, now: datetime, mode: str) -> None:
    if not user_id or "/" in user_id:
        raise ValueError("A concrete user ID is required")
    if now.tzinfo is None or now.utcoffset() is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise ValueError("--at must be an aware UTC timestamp")
    if mode not in {"rehearsal", "live"}:
        raise ValueError("mode must be rehearsal or live")


def _require_live_readiness() -> None:
    required = {
        "DEMO_PRECONSENTED_RECIPIENT": os.getenv("DEMO_PRECONSENTED_RECIPIENT"),
        "VAPI_PRIVATE_KEY": os.getenv("VAPI_PRIVATE_KEY"),
        "VAPI_WEBHOOK_SECRET": os.getenv("VAPI_WEBHOOK_SECRET"),
        "TWILIO_ACCOUNT_SID": os.getenv("TWILIO_ACCOUNT_SID"),
        "TWILIO_AUTH_TOKEN": os.getenv("TWILIO_AUTH_TOKEN"),
        "TWILIO_PHONE_NUMBER": os.getenv("TWILIO_PHONE_NUMBER"),
    }
    missing = ", ".join(name for name, value in required.items() if not value)
    if missing:
        raise RuntimeError(f"Live demo readiness is incomplete: {missing}")


async def seed_demo_data(*, user_id: str, now: datetime, mode: SeedMode) -> DemoSeedSummary:
    return await DemoSeeder(InMemoryDemoRepository()).seed_demo_data(
        user_id=user_id, now=now, mode=mode
    )


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the bounded Relay demo scenario")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--mode", choices=("rehearsal", "live"), required=True)
    parser.add_argument("--at", required=True, help="UTC timestamp, for example 2026-08-22T16:00:00Z")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = _parse_at(args.at)
    if args.dry_run:
        summary = asyncio.run(
            DemoSeeder(InMemoryDemoRepository()).seed_demo_data(
                user_id=args.user_id, now=now, mode=args.mode
            )
        )
    else:
        from google.cloud.firestore_v1 import AsyncClient

        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise SystemExit("GOOGLE_CLOUD_PROJECT is required unless --dry-run is used")
        client = AsyncClient(project=project)
        try:
            summary = asyncio.run(
                DemoSeeder(FirestoreDemoRepository(client)).seed_demo_data(
                    user_id=args.user_id, now=now, mode=args.mode
                )
            )
        finally:
            client.close()
    print(summary.model_dump_json())


if __name__ == "__main__":
    main()
