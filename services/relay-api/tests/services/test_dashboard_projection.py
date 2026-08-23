from datetime import datetime, timezone

import pytest

from app.contracts import ActionRecord, Approval, Commitment
from app.domain.impact import CandidateChange, CandidateKind, RepairCandidate, RepairPlan
from app.domain.product import OutcomeStatus
from app.repositories.product import DashboardSource
from app.services.dashboard_projection import DashboardProjectionService


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _commitment(commitment_id: str, starts_at: str, *, commitment_type: str | None = None) -> Commitment:
    start = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
    return Commitment(
        id=commitment_id,
        user_id="u1",
        source_event_key=f"event-{commitment_id}",
        summary=commitment_id.replace("_", " ").title(),
        starts_at=start,
        ends_at=start.replace(hour=start.hour + 1),
        type=commitment_type,
        correlation_id="corr-1",
    )


def _action(action_id: str, target_ref: str, state: str = "needs_user") -> ActionRecord:
    expiry = datetime(2026, 8, 22, 17, 0, tzinfo=timezone.utc)
    return ActionRecord(
        id=action_id,
        user_id="u1",
        repair_plan_id="plan-1",
        repair_plan_version=1,
        type="voice_call",
        target_ref=target_ref,
        idempotency_key=f"akey_{action_id}",
        authorization_snapshot={
            "type": "voice_call",
            "goal": "confirm a permitted time",
            "recipient_ref": "opaque-recipient",
            "identity_disclosure": "I am Relay.",
            "authorized_options": ["confirm_new_time"],
            "max_fee_inr": 0,
            "must_not": ["make_payment"],
            "required_evidence": ["confirmation"],
            "expires_at": expiry,
        },
        state=state,
        correlation_id="corr-1",
        updated_at=NOW,
    )


class FakeDashboardRepository:
    def __init__(self, source: DashboardSource) -> None:
        self.source = source

    async def get_dashboard_source(self, *, user_id: str) -> DashboardSource | None:
        assert user_id == "u1"
        return self.source

    async def get_action_audit_source(self, *, user_id: str, action_id: str):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_dashboard_orders_timeline_and_never_returns_encrypted_or_provider_data() -> None:
    commitments = (
        _commitment("dinner_1", "2026-08-22T22:00:00Z"),
        _commitment("flight_1", "2026-08-22T22:05:00Z"),
        _commitment("hotel_1", "2026-08-23T00:00:00Z"),
        _commitment("pickup_1", "2026-08-22T22:20:00Z", commitment_type="pickup"),
    )
    plan = RepairPlan(
        id="plan-1",
        version=1,
        assessment_id="assessment-1",
        selected_candidate_id="candidate-1",
        candidates=(
            RepairCandidate(
                id="candidate-1",
                kind=CandidateKind.CONFIRM_LATE_ARRIVAL,
                changes=(CandidateChange(commitment_id="dinner_1", kind=CandidateKind.RESCHEDULE),),
                invalid_reasons=(),
                explanation="safe",
            ),
        ),
        approval_id="approval-1",
        input_fingerprint="fingerprint",
    )
    approval = Approval(
        id="approval-1",
        user_id="u1",
        action_ids=["call-1"],
        state="awaiting_approval",
        version=1,
        correlation_id="corr-1",
        expires_at=datetime(2026, 8, 22, 17, 0, tzinfo=timezone.utc),
    )
    source = DashboardSource(
        generated_at=NOW,
        plan=plan,
        assessment=None,
        commitments=commitments,
        approval=approval,
        actions=(_action("call-1", "commitment:dinner_1"),),
        audits=(),
    )

    view = await DashboardProjectionService(FakeDashboardRepository(source), now=lambda: NOW).build_dashboard(user_id="u1")

    assert [item.commitment_id for item in view.timeline] == ["dinner_1", "flight_1", "hotel_1", "pickup_1"]
    assert view.timeline[0].status.value == "at_risk"
    rendered = view.model_dump_json()
    assert "enc:v1:" not in rendered
    assert "provider_ref" not in rendered
    assert view.outcomes[0].status is OutcomeStatus.NEEDS_USER
    assert "confirmed" not in view.outcomes[0].summary.lower()
