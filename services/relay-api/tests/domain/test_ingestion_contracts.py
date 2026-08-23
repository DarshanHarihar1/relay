from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.ingestion import (
    DisruptionCandidate,
    GmailMessage,
    GmailNotification,
    GoogleConnection,
    MatchResult,
    SelectedContact,
)


NOW = datetime(2026, 8, 23, tzinfo=timezone.utc)


def test_candidate_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError):
        DisruptionCandidate(
            change_type="flight_delay",
            provider=None,
            booking_reference=None,
            old_time=None,
            new_time=None,
            location_text=None,
            confidence=1.01,
            evidence_excerpt="Delay",
        )


def test_candidate_rejects_oversized_evidence_excerpt() -> None:
    with pytest.raises(ValidationError):
        DisruptionCandidate(
            change_type="other",
            provider=None,
            booking_reference=None,
            old_time=None,
            new_time=None,
            location_text=None,
            confidence=0.5,
            evidence_excerpt="x" * 501,
        )


def test_google_connection_requires_an_encrypted_refresh_token() -> None:
    with pytest.raises(ValidationError):
        GoogleConnection(
            user_id="user-1",
            granted_scopes=frozenset({"scope"}),
            encrypted_refresh_token="",
            connected_at=NOW,
        )


def test_gmail_message_keeps_only_normalized_message_fields() -> None:
    message = GmailMessage(
        id="message-1",
        thread_id="thread-1",
        history_id=123,
        internal_date=NOW,
        label_ids=frozenset({"Label_123"}),
        subject="Flight delayed",
        from_address="alerts@example.test",
        text_body="Your flight is delayed.",
    )

    assert message.label_ids == frozenset({"Label_123"})
    assert "raw_payload" not in GmailMessage.model_fields


def test_notification_requires_an_aware_publish_time() -> None:
    with pytest.raises(ValidationError):
        GmailNotification(
            email_address="person@example.test",
            history_id=123,
            published_at=datetime(2026, 8, 23),
        )


def test_selected_contact_retains_only_encrypted_phone_metadata() -> None:
    selected = SelectedContact(
        display_name="Rohan",
        encrypted_phone_number="encrypted-value",
        phone_last4="3210",
        source="google_picker",
        selected_at=NOW,
    )

    assert selected.phone_last4 == "3210"
    assert "phone_number" not in SelectedContact.model_fields


def test_match_result_requires_a_known_status() -> None:
    with pytest.raises(ValidationError):
        MatchResult(status="maybe", commitment_id=None, score=0, reasons=[])
