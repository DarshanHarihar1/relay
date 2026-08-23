from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.domain.ingestion import GoogleConnection
from app.security import FernetFieldCipher


@pytest.mark.asyncio
async def test_search_uses_people_search_contacts_and_returns_picker_safe_fields() -> None:
    from app.adapters.google_people import GooglePeopleAdapter

    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            assert request.method == "POST"
            return httpx.Response(200, json={"access_token": "access-token"})

        assert request.method == "GET"
        assert request.url.path == "/v1/people:searchContacts"
        assert request.url.params["query"] == "Rohan"
        assert request.url.params["pageSize"] == "20"
        assert request.url.params["readMask"] == "names,phoneNumbers,photos"
        assert request.headers["authorization"] == "Bearer access-token"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "person": {
                            "resourceName": "people/private-id",
                            "names": [{"displayName": "Rohan"}],
                            "emailAddresses": [{"value": "private@example.test"}],
                            "phoneNumbers": [{"type": "mobile", "value": "+919876543210"}],
                            "photos": [{"url": "https://example.test/rohan.jpg"}],
                        }
                    }
                ]
            },
        )

    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    adapter = GooglePeopleAdapter(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token_reader=lambda saved_connection: cipher.decrypt(
            saved_connection.encrypted_refresh_token
        ),
        transport=httpx.MockTransport(handler),
    )
    connection = GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/contacts.readonly"}),
        encrypted_refresh_token=cipher.encrypt("refresh-token"),
        connected_at=datetime.now(timezone.utc),
        contacts_picker_enabled=True,
    )

    contacts = await adapter.search_contacts(connection=connection, query="Rohan")

    assert len(calls) == 2
    assert contacts[0].model_dump() == {
        "display_name": "Rohan",
        "phones": [{"label": "mobile", "number": "+919876543210"}],
        "avatar_url": "https://example.test/rohan.jpg",
    }
    assert "resource_name" not in contacts[0].model_dump()
    assert "private@example.test" not in json.dumps(contacts[0].model_dump())
