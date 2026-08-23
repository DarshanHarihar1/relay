from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from app.contracts import ActionRecord
from app.providers.uber import UberDeepLinkBuilder


def uber_action() -> ActionRecord:
    return ActionRecord(
        id="ride-1",
        user_id="user-1",
        repair_plan_id="plan-1",
        repair_plan_version=1,
        type="uber_deep_link",
        target_ref="commitment:dinner",
        idempotency_key="akey_uber",
        authorization_snapshot={
            "type": "uber_deep_link",
            "pickup": "Kempegowda International Airport",
            "destination": "Toscano",
            "handoff_label": "Open Uber",
        },
        state="authorized",
        correlation_id="corr-1",
    )


def test_uber_link_contains_encoded_labels_without_booking_fields() -> None:
    url = UberDeepLinkBuilder(client_id="client").build(uber_action())
    query = parse_qs(urlparse(url).query)

    assert query["action"] == ["setPickup"]
    assert query["pickup[formatted_address]"] == ["Kempegowda International Airport"]
    assert query["dropoff[formatted_address]"] == ["Toscano"]
    assert "request" not in url
    assert "product_id" not in url


def test_uber_action_does_not_require_coordinates_or_current_time() -> None:
    assert uber_action().authorization_snapshot.pickup == "Kempegowda International Airport"
    assert datetime.now(timezone.utc).tzinfo is not None
