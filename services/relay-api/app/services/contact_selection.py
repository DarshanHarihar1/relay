from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.adapters.google_auth import CONTACTS_SCOPE
from app.domain.context import PickerContact
from app.domain.ingestion import GoogleConnection, SelectedContact
from app.ports.google import PeoplePort
from app.security import FieldCipher


class ContactChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    phone_number: str = Field(min_length=7, max_length=32)
    source: Literal["google_picker", "manual"]

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A display name is required")
        return value

    @field_validator("phone_number")
    @classmethod
    def normalize_phone_number(cls, value: str) -> str:
        raw = value.strip()
        digits = "".join(character for character in raw if character.isdigit())
        if not 7 <= len(digits) <= 15:
            raise ValueError("A valid phone number is required")
        return f"+{digits}" if raw.startswith("+") else digits


class ContactsPermissionRequired(Exception):
    pass


class GoogleConnectionReader(Protocol):
    async def get_connection(self, user_id: str) -> GoogleConnection | None: ...


class SelectedContactStore(Protocol):
    async def save_selected_contact(
        self, *, user_id: str, commitment_id: str, selected: SelectedContact
    ) -> None: ...

    async def remove_selected_contact(self, *, user_id: str, commitment_id: str) -> None: ...


class InMemorySelectedContactStore:
    """Development store whose methods model the required atomic persistence boundary."""

    def __init__(self) -> None:
        self._selected: dict[tuple[str, str], SelectedContact] = {}

    async def save_selected_contact(
        self, *, user_id: str, commitment_id: str, selected: SelectedContact
    ) -> None:
        self._selected[(user_id, commitment_id)] = selected

    async def remove_selected_contact(self, *, user_id: str, commitment_id: str) -> None:
        self._selected.pop((user_id, commitment_id), None)


class ContactSelectionService:
    _SEARCH_LIMIT = 10
    _SEARCH_WINDOW = timedelta(minutes=1)
    _PICKER_RESULT_TTL = timedelta(minutes=5)

    def __init__(
        self,
        *,
        connections: GoogleConnectionReader,
        people: PeoplePort,
        selections: SelectedContactStore,
        cipher: FieldCipher,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._connections = connections
        self._people = people
        self._selections = selections
        self._cipher = cipher
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._searches: dict[str, deque[datetime]] = {}
        self._current_choices: dict[str, tuple[datetime, set[tuple[str, str]]]] = {}

    async def search_picker_contacts(self, *, user_id: str, query: str) -> list[PickerContact]:
        normalized_query = " ".join(query.split())
        if len(normalized_query) < 2:
            raise ValueError("Contact search requires at least two characters")
        connection = await self._contacts_connection(user_id)
        self._record_search(user_id)
        contacts = await self._people.search_contacts(
            connection=connection, query=normalized_query, page_size=20
        )
        self._current_choices[user_id] = (
            self._now(),
            {
                (contact.display_name, phone.number)
                for contact in contacts
                for phone in contact.phones
            },
        )
        return contacts

    async def select_pickup_contact(
        self, *, user_id: str, commitment_id: str, choice: ContactChoice
    ) -> SelectedContact:
        self._validate_commitment_id(commitment_id)
        if choice.source == "google_picker":
            await self._contacts_connection(user_id)
            self._require_current_picker_choice(user_id, choice)
        selected = SelectedContact(
            display_name=choice.display_name,
            encrypted_phone_number=self._cipher.encrypt(choice.phone_number),
            phone_last4="".join(character for character in choice.phone_number if character.isdigit())[-4:],
            source=choice.source,
            selected_at=self._now(),
        )
        await self._selections.save_selected_contact(
            user_id=user_id, commitment_id=commitment_id, selected=selected
        )
        return selected

    async def remove_pickup_contact(self, *, user_id: str, commitment_id: str) -> None:
        self._validate_commitment_id(commitment_id)
        await self._selections.remove_selected_contact(user_id=user_id, commitment_id=commitment_id)

    async def _contacts_connection(self, user_id: str) -> GoogleConnection:
        connection = await self._connections.get_connection(user_id)
        if (
            connection is None
            or not connection.contacts_picker_enabled
            or CONTACTS_SCOPE not in connection.granted_scopes
        ):
            raise ContactsPermissionRequired
        return connection

    def _record_search(self, user_id: str) -> None:
        now = self._now()
        searches = self._searches.setdefault(user_id, deque())
        oldest_allowed = now - self._SEARCH_WINDOW
        while searches and searches[0] <= oldest_allowed:
            searches.popleft()
        if len(searches) >= self._SEARCH_LIMIT:
            raise ValueError("Contact search rate limit exceeded")
        searches.append(now)

    def _require_current_picker_choice(self, user_id: str, choice: ContactChoice) -> None:
        current = self._current_choices.get(user_id)
        if current is None:
            raise ValueError("Choose a phone from the current picker results")
        searched_at, choices = current
        if self._now() - searched_at > self._PICKER_RESULT_TTL:
            self._current_choices.pop(user_id, None)
            raise ValueError("Choose a phone from the current picker results")
        if (choice.display_name, choice.phone_number) not in choices:
            raise ValueError("Choose a phone from the current picker results")

    @staticmethod
    def _validate_commitment_id(commitment_id: str) -> None:
        if not commitment_id or "/" in commitment_id:
            raise ValueError("A valid commitment ID is required")
