"""The stores that must survive a restart and a second Cloud Run instance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.contracts import SourceEventEnvelope
from app.domain.ingestion import GoogleConnection, SelectedContact


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


def _connection(user_id: str, mailbox: str | None = "mailbox@example.test") -> GoogleConnection:
    return GoogleConnection(
        user_id=user_id,
        granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
        gmail_label_id="Label_123",
        gmail_email_address=mailbox,
        gmail_history_id=800,
        encrypted_refresh_token="cipher:token",
        connected_at=NOW,
    )


@pytest.fixture
def store(firestore_client):
    from app.repositories.google_connections import FirestoreGoogleStore

    return FirestoreGoogleStore(firestore_client)


@pytest.mark.emulator
async def test_a_connection_survives_a_new_store_instance(firestore_client, store) -> None:
    from app.repositories.google_connections import FirestoreGoogleStore

    user_id = f"u-{uuid4().hex}"
    await store.put_connection(_connection(user_id))

    restarted = FirestoreGoogleStore(firestore_client)
    found = await restarted.get_connection(user_id)

    assert found is not None
    assert found.gmail_email_address == "mailbox@example.test"
    assert found.gmail_history_id == 800


@pytest.mark.emulator
async def test_a_state_nonce_can_only_be_consumed_once(store) -> None:
    nonce = uuid4().hex
    await store.create_state_nonce(nonce, datetime.now(timezone.utc) + timedelta(minutes=5))

    assert await store.consume_state_nonce(nonce) is True
    assert await store.consume_state_nonce(nonce) is False


@pytest.mark.emulator
async def test_an_expired_state_nonce_is_rejected(store) -> None:
    nonce = uuid4().hex
    await store.create_state_nonce(nonce, datetime.now(timezone.utc) - timedelta(seconds=1))

    assert await store.consume_state_nonce(nonce) is False


@pytest.mark.emulator
async def test_a_mailbox_resolves_to_its_single_connection(store) -> None:
    mailbox = f"{uuid4().hex}@example.test"
    user_id = f"u-{uuid4().hex}"
    await store.put_connection(_connection(user_id, mailbox))

    found = await store.get_active_connections_by_gmail_email(mailbox)

    assert [c.user_id for c in found] == [user_id]


@pytest.mark.emulator
async def test_an_unknown_mailbox_resolves_to_nothing(store) -> None:
    assert await store.get_active_connections_by_gmail_email("nobody@example.test") == []


@pytest.mark.emulator
async def test_a_disconnect_removes_the_connection(store) -> None:
    user_id = f"u-{uuid4().hex}"
    await store.put_connection(_connection(user_id))
    await store.delete_connection(user_id)

    assert await store.get_connection(user_id) is None


@pytest.mark.emulator
async def test_a_source_event_can_be_claimed_only_once(firestore_client) -> None:
    from app.repositories.gmail_ingestion import FirestoreGmailIngestionRepository
    from app.repositories.google_connections import FirestoreGoogleStore

    user_id = f"u-{uuid4().hex}"
    google = FirestoreGoogleStore(firestore_client)
    await google.put_connection(_connection(user_id))
    repository = FirestoreGmailIngestionRepository(firestore_client, connections=google)
    event = SourceEventEnvelope(
        source="gmail",
        source_event_key="gmail:m1:900",
        occurred_at=NOW,
        payload={"message_id": "m1", "history_id": "900"},
        correlation_id="corr-1",
    )

    first = await repository.claim_source_event(user_id=user_id, event=event)
    second = await repository.claim_source_event(user_id=user_id, event=event)

    assert (first, second) == (True, False)


@pytest.mark.emulator
async def test_a_released_claim_can_be_retried(firestore_client) -> None:
    from app.repositories.gmail_ingestion import FirestoreGmailIngestionRepository
    from app.repositories.google_connections import FirestoreGoogleStore

    user_id = f"u-{uuid4().hex}"
    google = FirestoreGoogleStore(firestore_client)
    await google.put_connection(_connection(user_id))
    repository = FirestoreGmailIngestionRepository(firestore_client, connections=google)
    event = SourceEventEnvelope(
        source="gmail",
        source_event_key="gmail:m2:900",
        occurred_at=NOW,
        payload={"message_id": "m2", "history_id": "900"},
        correlation_id="corr-1",
    )

    await repository.claim_source_event(user_id=user_id, event=event)
    await repository.release_source_event_claim(
        user_id=user_id, source_event_key="gmail:m2:900"
    )

    assert await repository.claim_source_event(user_id=user_id, event=event) is True


@pytest.mark.emulator
async def test_the_cursor_never_moves_backwards(firestore_client) -> None:
    from app.repositories.gmail_ingestion import FirestoreGmailIngestionRepository
    from app.repositories.google_connections import FirestoreGoogleStore

    user_id = f"u-{uuid4().hex}"
    google = FirestoreGoogleStore(firestore_client)
    await google.put_connection(_connection(user_id))
    repository = FirestoreGmailIngestionRepository(firestore_client, connections=google)
    mailbox = "mailbox@example.test"

    assert await repository.get_gmail_cursor(user_id=user_id, mailbox=mailbox) == 800
    assert await repository.update_gmail_cursor_if_newer(
        user_id=user_id, mailbox=mailbox, proposed_history_id=900
    ) == 900
    assert await repository.update_gmail_cursor_if_newer(
        user_id=user_id, mailbox=mailbox, proposed_history_id=850
    ) == 900


@pytest.mark.emulator
async def test_a_selected_contact_is_stored_and_removed(firestore_client) -> None:
    from app.repositories.selected_contacts import FirestoreSelectedContactStore

    user_id = f"u-{uuid4().hex}"
    store = FirestoreSelectedContactStore(firestore_client)
    selected = SelectedContact(
        display_name="Aunt Meera",
        encrypted_phone_number="cipher:phone",
        phone_last4="4321",
        source="google_picker",
        selected_at=NOW,
    )

    await store.save_selected_contact(
        user_id=user_id, commitment_id="pickup_1", selected=selected
    )
    stored = await store.get_selected_contact(user_id=user_id, commitment_id="pickup_1")
    await store.remove_selected_contact(user_id=user_id, commitment_id="pickup_1")

    assert stored is not None
    assert stored.phone_last4 == "4321"
    assert "9876" not in stored.encrypted_phone_number
    assert await store.get_selected_contact(user_id=user_id, commitment_id="pickup_1") is None
