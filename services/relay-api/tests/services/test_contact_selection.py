from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.context import PickerContact, PickerPhone
from app.domain.ingestion import GoogleConnection
from app.security import FernetFieldCipher


class FakePeople:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def search_contacts(self, *, connection, query: str, page_size: int = 20):
        self.calls.append(("searchContacts", query, page_size))
        return [
            PickerContact(
                display_name="Rohan",
                phones=[PickerPhone(label="mobile", number="+919876543210")],
                avatar_url=None,
            )
        ]


class FakeConnections:
    def __init__(self, connection: GoogleConnection | None) -> None:
        self.connection = connection

    async def get_connection(self, user_id: str) -> GoogleConnection | None:
        assert user_id == "u1"
        return self.connection


class FakeSelections:
    def __init__(self) -> None:
        self.last_document: dict[str, object] | None = None
        self.deleted: tuple[str, str] | None = None

    async def save_selected_contact(self, *, user_id: str, commitment_id: str, selected) -> None:
        self.last_document = selected.model_dump()

    async def remove_selected_contact(self, *, user_id: str, commitment_id: str) -> None:
        self.deleted = (user_id, commitment_id)


def _connection(*, contacts_enabled: bool = True) -> GoogleConnection:
    scopes = {"https://www.googleapis.com/auth/gmail.readonly"}
    if contacts_enabled:
        scopes.add("https://www.googleapis.com/auth/contacts.readonly")
    return GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset(scopes),
        encrypted_refresh_token="enc:v1:token",
        connected_at=datetime.now(timezone.utc),
        contacts_picker_enabled=contacts_enabled,
    )


@pytest.mark.asyncio
async def test_search_uses_search_contacts_not_bulk_list() -> None:
    from app.services.contact_selection import ContactSelectionService

    fake_people = FakePeople()
    service = ContactSelectionService(
        connections=FakeConnections(_connection()),
        people=fake_people,
        selections=FakeSelections(),
        cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
    )

    await service.search_picker_contacts(user_id="u1", query="Rohan")

    assert fake_people.calls == [("searchContacts", "Rohan", 20)]


@pytest.mark.asyncio
async def test_selection_stores_only_selected_encrypted_phone() -> None:
    from app.services.contact_selection import ContactChoice, ContactSelectionService

    repository = FakeSelections()
    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    service = ContactSelectionService(
        connections=FakeConnections(_connection()),
        people=FakePeople(),
        selections=repository,
        cipher=cipher,
    )
    await service.search_picker_contacts(user_id="u1", query="Rohan")

    saved = await service.select_pickup_contact(
        user_id="u1",
        commitment_id="pickup1",
        choice=ContactChoice(
            display_name="Rohan", phone_number="+919876543210", source="google_picker"
        ),
    )

    assert saved.phone_last4 == "3210"
    assert repository.last_document is not None
    assert repository.last_document["display_name"] == "Rohan"
    assert repository.last_document["encrypted_phone_number"] != "+919876543210"
    assert repository.last_document["phone_last4"] == "3210"
    assert repository.last_document["source"] == "google_picker"
    assert "selected_at" in repository.last_document


@pytest.mark.asyncio
async def test_search_requires_contacts_permission() -> None:
    from app.services.contact_selection import ContactsPermissionRequired, ContactSelectionService

    service = ContactSelectionService(
        connections=FakeConnections(_connection(contacts_enabled=False)),
        people=FakePeople(),
        selections=FakeSelections(),
        cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
    )

    with pytest.raises(ContactsPermissionRequired):
        await service.search_picker_contacts(user_id="u1", query="Rohan")


@pytest.mark.asyncio
async def test_picker_selection_rejects_phone_not_in_current_result() -> None:
    from app.services.contact_selection import ContactChoice, ContactSelectionService

    service = ContactSelectionService(
        connections=FakeConnections(_connection()),
        people=FakePeople(),
        selections=FakeSelections(),
        cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
    )
    await service.search_picker_contacts(user_id="u1", query="Rohan")

    with pytest.raises(ValueError, match="current picker"):
        await service.select_pickup_contact(
            user_id="u1",
            commitment_id="pickup1",
            choice=ContactChoice(display_name="Rohan", phone_number="+919800000000", source="google_picker"),
        )
