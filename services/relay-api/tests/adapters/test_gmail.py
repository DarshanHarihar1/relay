from __future__ import annotations

from datetime import datetime, timezone

import json

import httpx
import pytest

from app.domain.ingestion import GoogleConnection
from app.security import FernetFieldCipher


@pytest.mark.asyncio
async def test_watch_is_limited_to_the_configured_label() -> None:
    from app.adapters.gmail import GmailAdapter

    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access-token"})
        assert request.method == "POST"
        assert request.url.path == "/gmail/v1/users/me/watch"
        assert request.headers["authorization"] == "Bearer access-token"
        assert json.loads(request.content) == {
            "topicName": "projects/relay/topics/gmail-events",
            "labelIds": ["Label_123"],
            "labelFilterBehavior": "INCLUDE",
        }
        return httpx.Response(200, json={"historyId": "900", "expiration": "1760000000000"})

    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    adapter = GmailAdapter(
        client_id="client-id",
        client_secret="client-secret",
        topic="projects/relay/topics/gmail-events",
        refresh_token_reader=lambda connection: cipher.decrypt(connection.encrypted_refresh_token),
        transport=httpx.MockTransport(handler),
    )
    connection = GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
        gmail_label_id="Label_123",
        encrypted_refresh_token=cipher.encrypt("refresh-token"),
        connected_at=datetime.now(timezone.utc),
    )

    registration = await adapter.ensure_watch(connection=connection)

    assert registration.history_id == 900
    assert registration.request["labelIds"] == ["Label_123"]
    assert registration.request["labelFilterBehavior"] == "INCLUDE"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_history_pages_and_message_bodies_are_label_scoped_and_sanitized() -> None:
    from app.adapters.gmail import GmailAdapter

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url.path.endswith("/history"):
            assert request.url.params["startHistoryId"] == "800"
            assert request.url.params["labelId"] == "Label_123"
            assert request.url.params.get_list("historyTypes") == ["messageAdded", "labelAdded"]
            return httpx.Response(
                200,
                json={
                    "historyId": "900",
                    "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}],
                    "nextPageToken": "next-page",
                },
            )
        assert request.url.path.endswith("/messages/m1")
        assert request.url.params["format"] == "full"
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "historyId": "900",
                "internalDate": "1760000000000",
                "labelIds": ["Label_123"],
                "payload": {
                    "mimeType": "text/html",
                    "headers": [
                        {"name": "Subject", "value": "Flight update"},
                        {"name": "From", "value": "Airline <updates@example.test>"},
                    ],
                    "body": {"data": "PGI-RGVsYXllZCBieSA0NSBtaW51dGVzPC9iPg"},
                },
            },
        )

    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    adapter = GmailAdapter(
        client_id="client-id",
        client_secret="client-secret",
        topic="projects/relay/topics/gmail-events",
        refresh_token_reader=lambda connection: cipher.decrypt(connection.encrypted_refresh_token),
        transport=httpx.MockTransport(handler),
    )
    connection = GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
        gmail_label_id="Label_123",
        encrypted_refresh_token=cipher.encrypt("refresh-token"),
        connected_at=datetime.now(timezone.utc),
    )

    history = await adapter.list_history(connection=connection, start_history_id=800)
    message = await adapter.get_message(connection=connection, message_id="m1")

    assert history.added_message_ids == ["m1"]
    assert history.next_page_token == "next-page"
    assert message.text_body == "Delayed by 45 minutes"
    assert message.label_ids == frozenset({"Label_123"})


@pytest.mark.asyncio
async def test_resync_is_bounded_to_the_label_and_last_48_hours() -> None:
    from app.adapters.gmail import GmailAdapter

    queries: list[httpx.QueryParams] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access-token"})
        if request.url.path.endswith("/profile"):
            return httpx.Response(
                200, json={"emailAddress": "mailbox@example.test", "historyId": "950"}
            )
        queries.append(request.url.params)
        return httpx.Response(200, json={"messages": [{"id": "m9"}]})

    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    adapter = GmailAdapter(
        client_id="client-id",
        client_secret="client-secret",
        topic="projects/relay/topics/gmail-events",
        refresh_token_reader=lambda connection: cipher.decrypt(connection.encrypted_refresh_token),
        transport=httpx.MockTransport(handler),
    )
    connection = GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
        gmail_label_id="Label_123",
        encrypted_refresh_token=cipher.encrypt("refresh-token"),
        connected_at=datetime.now(timezone.utc),
    )
    since = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)

    page = await adapter.resync_last_48_hours(connection=connection, since=since)

    assert len(queries) == 1
    assert queries[0]["labelIds"] == "Label_123"
    assert queries[0]["q"] == f"after:{int(since.timestamp())}"
    assert page.added_message_ids == ["m9"]
    assert page.history_id == 950


@pytest.mark.asyncio
async def test_profile_resolves_the_mailbox_and_current_history_id() -> None:
    from app.adapters.gmail import GmailAdapter

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access-token"})
        assert request.url.path.endswith("/profile")
        return httpx.Response(
            200, json={"emailAddress": "mailbox@example.test", "historyId": "900"}
        )

    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    adapter = GmailAdapter(
        client_id="client-id",
        client_secret="client-secret",
        topic="projects/relay/topics/gmail-events",
        refresh_token_reader=lambda connection: cipher.decrypt(connection.encrypted_refresh_token),
        transport=httpx.MockTransport(handler),
    )
    connection = GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
        gmail_label_id="Label_123",
        encrypted_refresh_token=cipher.encrypt("refresh-token"),
        connected_at=datetime.now(timezone.utc),
    )

    profile = await adapter.get_profile(connection=connection)

    assert profile.email_address == "mailbox@example.test"
    assert profile.history_id == 900


@pytest.mark.asyncio
async def test_history_catches_a_label_applied_after_the_message_already_arrived() -> None:
    """A user labelling mail manually (or via a delayed filter) produces a
    labelAdded history event, not messageAdded. Both must be treated as new
    work, or manually-labelled mail is silently never ingested."""
    from app.adapters.gmail import GmailAdapter

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "access-token"})
        assert request.url.params.get_list("historyTypes") == ["messageAdded", "labelAdded"]
        return httpx.Response(
            200,
            json={
                "historyId": "900",
                "history": [
                    {
                        "id": "850",
                        "labelsAdded": [
                            {
                                "message": {"id": "m2", "labelIds": ["Label_123", "INBOX"]},
                                "labelIds": ["Label_123"],
                            }
                        ],
                    }
                ],
            },
        )

    cipher = FernetFieldCipher(FernetFieldCipher.generate_key())
    adapter = GmailAdapter(
        client_id="client-id",
        client_secret="client-secret",
        topic="projects/relay/topics/gmail-events",
        refresh_token_reader=lambda connection: cipher.decrypt(connection.encrypted_refresh_token),
        transport=httpx.MockTransport(handler),
    )
    connection = GoogleConnection(
        user_id="u1",
        granted_scopes=frozenset({"https://www.googleapis.com/auth/gmail.readonly"}),
        gmail_label_id="Label_123",
        encrypted_refresh_token=cipher.encrypt("refresh-token"),
        connected_at=datetime.now(timezone.utc),
    )

    history = await adapter.list_history(connection=connection, start_history_id=800)

    assert history.added_message_ids == ["m2"]
