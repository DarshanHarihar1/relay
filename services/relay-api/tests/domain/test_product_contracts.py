from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from app.domain.product import ActionOutcomeView, PickupContactCommand, PlanTimelineItem


def test_pickup_command_rejects_mixed_selection_fields() -> None:
    with pytest.raises(ValidationError):
        PickupContactCommand(
            selection="no_pickup",
            manual_phone_number="+919999999999",
            expected_version=1,
        )


def test_pickup_command_accepts_only_one_explicit_selection() -> None:
    assert PickupContactCommand(selection="no_pickup", expected_version=1).selection == "no_pickup"
    assert PickupContactCommand(
        selection="google_picker",
        picker_session_id="picker-1",
        picker_contact_index=0,
        expected_version=1,
    ).selection == "google_picker"


def test_outcome_view_excludes_internal_action_fields() -> None:
    assert "provider_ref" not in ActionOutcomeView.model_fields
    assert "idempotency_key" not in ActionOutcomeView.model_fields


def test_product_timestamps_must_be_utc_and_aware() -> None:
    with pytest.raises(ValidationError):
        PlanTimelineItem(
            commitment_id="c1",
            title="Arrival",
            starts_at="2026-08-22T22:05:00",
            ends_at="2026-08-22T22:20:00Z",
            status="changed",
            explanation="Flight delay",
        )
    with pytest.raises(ValidationError):
        PlanTimelineItem(
            commitment_id="c1",
            title="Arrival",
            starts_at=datetime(2026, 8, 22, 22, 5, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            ends_at=datetime(2026, 8, 22, 22, 20, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            status="changed",
            explanation="Flight delay",
        )
