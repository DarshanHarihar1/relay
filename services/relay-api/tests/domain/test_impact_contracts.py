from datetime import datetime, timezone

from app.contracts import VoiceCallAuthorizationSnapshot
from app.domain.impact import ActionKind, make_action_idempotency_key, sha256_id


def test_canonical_ids_are_independent_of_mapping_insertion_order() -> None:
    assert sha256_id("impact", {"b": 2, "a": 1}) == sha256_id("impact", {"a": 1, "b": 2})


def test_action_key_changes_when_authorized_bounds_change() -> None:
    base = VoiceCallAuthorizationSnapshot(
        type="voice_call",
        goal="Confirm the new reservation time",
        recipient_ref="place_toscano_blr",
        identity_disclosure="I am Relay, an assistant calling on Darshan's behalf.",
        authorized_options=["23:15"],
        max_fee_inr=0,
        must_not=["make_payment"],
        required_evidence=["confirmation_time"],
        expires_at=datetime(2026, 8, 22, 18, tzinfo=timezone.utc),
    )
    changed = base.model_copy(update={"max_fee_inr": 1})
    original_key = make_action_idempotency_key(1, ActionKind.CALL_VENUE, base.recipient_ref, base)
    changed_key = make_action_idempotency_key(1, ActionKind.CALL_VENUE, changed.recipient_ref, changed)
    assert original_key != changed_key
