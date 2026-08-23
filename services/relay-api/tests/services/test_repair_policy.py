from datetime import datetime, timedelta, timezone

from app.contracts import VoiceCallAuthorizationSnapshot
from app.domain.impact import ActionKind, CandidateChange, CandidateKind, PolicyDecision, RepairCandidate
from app.services.repair_policy import PolicyEngine, UserActionPolicy

USER_ID = "u1"
EXPIRES = datetime(2026, 8, 22, 20, tzinfo=timezone.utc)

engine = PolicyEngine()


def call_bounds() -> VoiceCallAuthorizationSnapshot:
    return VoiceCallAuthorizationSnapshot(
        type="voice_call",
        goal="Confirm the new reservation time",
        recipient_ref="place_toscano_blr",
        identity_disclosure="I am Relay, an assistant calling on Darshan's behalf.",
        authorized_options=["23:15"],
        max_fee_inr=0,
        must_not=["make_payment"],
        required_evidence=["confirmation_time"],
        expires_at=EXPIRES,
    )


def candidate_with_actions(*kinds: ActionKind) -> RepairCandidate:
    change = CandidateChange(
        commitment_id="dinner",
        kind=CandidateKind.RESCHEDULE,
        action_kinds=kinds,
        target_ref="place_toscano_blr",
    )
    return RepairCandidate(
        id="candidate-dinner-reschedule",
        kind=CandidateKind.RESCHEDULE,
        changes=(change,),
        invalid_reasons=(),
        explanation="test",
    )


def test_voice_action_always_requires_explicit_approval() -> None:
    policy = UserActionPolicy(auto_action_kinds=(ActionKind.CALL_VENUE,), max_auto_fee_inr=10_000)
    assert engine.decide(ActionKind.CALL_VENUE, call_bounds(), policy) is PolicyDecision.ASK


def test_never_policy_blocks_the_entire_candidate_batch() -> None:
    records, approval = engine.create_batch(
        user_id=USER_ID,
        repair_plan_id="plan_1",
        repair_plan_version=1,
        candidate=candidate_with_actions(ActionKind.CALL_VENUE),
        policy=UserActionPolicy(never_action_kinds=(ActionKind.CALL_VENUE,)),
        expires_at=EXPIRES,
        correlation_id="corr-1",
    )
    assert records == ()
    assert approval is None


def test_ask_actions_are_sorted_and_share_one_expiry() -> None:
    records, approval = engine.create_batch(
        user_id=USER_ID,
        repair_plan_id="plan_1",
        repair_plan_version=1,
        candidate=candidate_with_actions(ActionKind.CALL_HOTEL, ActionKind.CALL_VENUE),
        policy=UserActionPolicy(),
        expires_at=EXPIRES,
        correlation_id="corr-1",
    )
    assert [record.authorization_snapshot.type for record in records] == ["voice_call", "voice_call"]
    assert all(record.state.value == "awaiting_approval" for record in records)
    assert approval is not None
    assert approval.action_ids == [record.id for record in records]
    assert approval.expires_at == EXPIRES


def test_auto_action_is_authorized_directly_when_no_ask_action_is_present() -> None:
    change = CandidateChange(
        commitment_id="dinner",
        kind=CandidateKind.RESCHEDULE,
        action_kinds=(ActionKind.CREATE_CALENDAR_HOLD,),
        target_ref="calendar_primary",
        proposed_start=EXPIRES,
        proposed_end=EXPIRES + timedelta(hours=1),
    )
    candidate = RepairCandidate(
        id="candidate-dinner-hold",
        kind=CandidateKind.RESCHEDULE,
        changes=(change,),
        invalid_reasons=(),
        explanation="test",
    )
    records, approval = engine.create_batch(
        user_id=USER_ID,
        repair_plan_id="plan_1",
        repair_plan_version=1,
        candidate=candidate,
        policy=UserActionPolicy(auto_action_kinds=(ActionKind.CREATE_CALENDAR_HOLD,)),
        expires_at=EXPIRES,
        correlation_id="corr-1",
    )
    assert records[0].state.value == "authorized"
    assert approval is None
