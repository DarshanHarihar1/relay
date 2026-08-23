from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.domain.context import PickerContact, PickerPhone
from app.domain.ingestion import GoogleConnection


_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_PEOPLE_SEARCH_URL = "https://people.googleapis.com/v1/people:searchContacts"
_PICKER_READ_MASK = "names,phoneNumbers,photos"


class GooglePeopleAdapter:
    """Minimal Google People client for the explicit, query-only contact picker."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token_reader: Callable[[GoogleConnection], str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token_reader = refresh_token_reader
        self._transport = transport

    async def search_contacts(
        self,
        *,
        connection: GoogleConnection,
        query: str,
        page_size: int = 20,
    ) -> list[PickerContact]:
        access_token = await self._access_token(connection)
        async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
            response = await client.get(
                _PEOPLE_SEARCH_URL,
                params={"query": query, "pageSize": page_size, "readMask": _PICKER_READ_MASK},
                headers={"Authorization": f"Bearer {access_token}"},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Google People returned an invalid response")
        return self._picker_contacts(payload.get("results", []))

    async def _access_token(self, connection: GoogleConnection) -> str:
        refresh_token = self._refresh_token_reader(connection)
        async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
            response = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        response.raise_for_status()
        payload = response.json()
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Google did not return an access token")
        return access_token

    @staticmethod
    def _picker_contacts(results: object) -> list[PickerContact]:
        if not isinstance(results, list):
            return []
        contacts: list[PickerContact] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            person = result.get("person")
            if not isinstance(person, dict):
                continue
            display_name = GooglePeopleAdapter._display_name(person)
            phones = GooglePeopleAdapter._phones(person)
            if display_name is None or not phones:
                continue
            contacts.append(
                PickerContact(
                    display_name=display_name,
                    phones=phones,
                    avatar_url=GooglePeopleAdapter._avatar_url(person),
                )
            )
        return contacts

    @staticmethod
    def _display_name(person: dict[str, Any]) -> str | None:
        names = person.get("names")
        if not isinstance(names, list):
            return None
        for name in names:
            value = name.get("displayName") if isinstance(name, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _phones(person: dict[str, Any]) -> list[PickerPhone]:
        phone_numbers = person.get("phoneNumbers")
        if not isinstance(phone_numbers, list):
            return []
        phones: list[PickerPhone] = []
        for phone in phone_numbers:
            if not isinstance(phone, dict):
                continue
            value = phone.get("value")
            if not isinstance(value, str) or not value.strip():
                continue
            label = phone.get("type")
            phones.append(PickerPhone(label=label if isinstance(label, str) else None, number=value.strip()))
        return phones

    @staticmethod
    def _avatar_url(person: dict[str, Any]) -> str | None:
        photos = person.get("photos")
        if not isinstance(photos, list):
            return None
        for photo in photos:
            url = photo.get("url") if isinstance(photo, dict) else None
            if isinstance(url, str) and url.strip():
                return url.strip()
        return None
