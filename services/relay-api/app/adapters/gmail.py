from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

from app.adapters.errors import RetryableProviderError, TerminalProviderError
from app.domain.ingestion import (
    GmailMessage,
    GmailProfile,
    GoogleConnection,
    HistoryPage,
    WatchRegistration,
)


_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailRetryableError(RetryableProviderError):
    """A Gmail failure that can be retried by the bounded worker policy."""


class GmailHistoryExpiredError(Exception):
    """Gmail no longer retains the requested history cursor."""


class GmailTerminalError(TerminalProviderError):
    """A Gmail failure that must be audited without another provider retry."""


class UnsupportedGmailMessageError(GmailTerminalError):
    pass


class _HtmlToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


class GmailAdapter:
    """Gmail REST adapter. All Gmail provider calls are confined to this class."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        topic: str,
        refresh_token_reader: Callable[[GoogleConnection], str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._topic = topic
        self._refresh_token_reader = refresh_token_reader
        self._transport = transport

    async def get_profile(self, *, connection: GoogleConnection) -> GmailProfile:
        """Resolve the mailbox identity from Google, never from a push payload."""
        payload = await self._request(
            connection=connection,
            method="GET",
            url=f"{_GMAIL_API_URL}/profile",
        )
        return GmailProfile(
            email_address=self._string_field(payload, "emailAddress"),
            history_id=self._int_field(payload, "historyId"),
        )

    async def ensure_watch(self, *, connection: GoogleConnection) -> WatchRegistration:
        label_id = self._label_id(connection)
        request = {
            "topicName": self._topic,
            "labelIds": [label_id],
            "labelFilterBehavior": "INCLUDE",
        }
        payload = await self._request(
            connection=connection,
            method="POST",
            url=f"{_GMAIL_API_URL}/watch",
            json=request,
        )
        history_id = self._int_field(payload, "historyId")
        expiration = self._int_field(payload, "expiration")
        return WatchRegistration(
            history_id=history_id,
            expires_at=datetime.fromtimestamp(expiration / 1000, tz=timezone.utc),
            request=request,
        )

    async def list_history(
        self,
        *,
        connection: GoogleConnection,
        start_history_id: int,
        page_token: str | None = None,
    ) -> HistoryPage:
        params: list[tuple[str, str]] = [
            ("startHistoryId", str(start_history_id)),
            # A message can carry the watched label either at delivery (a Gmail
            # filter applied it, producing messageAdded) or afterward (the user
            # or a delayed rule applied it, producing labelAdded). Both count.
            ("historyTypes", "messageAdded"),
            ("historyTypes", "labelAdded"),
            ("labelId", self._label_id(connection)),
        ]
        if page_token:
            params.append(("pageToken", page_token))
        payload = await self._request(
            connection=connection,
            method="GET",
            url=f"{_GMAIL_API_URL}/history",
            params=params,
        )
        added_message_ids: list[str] = []
        history = payload.get("history", [])
        if not isinstance(history, list):
            raise GmailTerminalError("Gmail history response was malformed")
        for item in history:
            if not isinstance(item, dict):
                continue
            for field in ("messagesAdded", "labelsAdded"):
                entries = item.get(field, [])
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    message = entry.get("message") if isinstance(entry, dict) else None
                    message_id = message.get("id") if isinstance(message, dict) else None
                    if isinstance(message_id, str) and message_id:
                        added_message_ids.append(message_id)
        next_page_token = payload.get("nextPageToken")
        if next_page_token is not None and not isinstance(next_page_token, str):
            raise GmailTerminalError("Gmail history response was malformed")
        return HistoryPage(
            history_id=self._int_field(payload, "historyId"),
            added_message_ids=added_message_ids,
            next_page_token=next_page_token,
        )

    async def get_message(self, *, connection: GoogleConnection, message_id: str) -> GmailMessage:
        payload = await self._request(
            connection=connection,
            method="GET",
            url=f"{_GMAIL_API_URL}/messages/{message_id}",
            params={"format": "full"},
        )
        message_payload = payload.get("payload")
        if not isinstance(message_payload, dict):
            raise UnsupportedGmailMessageError("Gmail message has no supported MIME payload")
        label_ids = payload.get("labelIds")
        if not isinstance(label_ids, list) or not all(isinstance(label, str) for label in label_ids):
            raise GmailTerminalError("Gmail message labels were malformed")
        headers = self._headers(message_payload)
        return GmailMessage(
            id=self._string_field(payload, "id"),
            thread_id=self._string_field(payload, "threadId"),
            history_id=self._int_field(payload, "historyId"),
            internal_date=datetime.fromtimestamp(
                self._int_field(payload, "internalDate") / 1000, tz=timezone.utc
            ),
            label_ids=frozenset(label_ids),
            subject=headers.get("subject", ""),
            from_address=headers.get("from", "unknown@invalid"),
            text_body=self._message_text(message_payload),
        )

    async def resync_last_48_hours(
        self, *, connection: GoogleConnection, since: datetime
    ) -> HistoryPage:
        """Perform the one bounded recovery scan allowed after history expiry."""
        page_token: str | None = None
        message_ids: list[str] = []
        while True:
            params: dict[str, str] = {
                "labelIds": self._label_id(connection),
                "q": f"after:{int(since.timestamp())}",
                "maxResults": "100",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = await self._request(
                connection=connection,
                method="GET",
                url=f"{_GMAIL_API_URL}/messages",
                params=params,
            )
            messages = payload.get("messages", [])
            if not isinstance(messages, list):
                raise GmailTerminalError("Gmail resync response was malformed")
            for message in messages:
                message_id = message.get("id") if isinstance(message, dict) else None
                if isinstance(message_id, str) and message_id:
                    message_ids.append(message_id)
            next_page_token = payload.get("nextPageToken")
            if next_page_token is None:
                break
            if not isinstance(next_page_token, str) or not next_page_token:
                raise GmailTerminalError("Gmail resync response was malformed")
            page_token = next_page_token
        profile = await self.get_profile(connection=connection)
        return HistoryPage(history_id=profile.history_id, added_message_ids=message_ids)

    async def _request(
        self,
        *,
        connection: GoogleConnection,
        method: str,
        url: str,
        params: Any = None,
        json: Any = None,
    ) -> dict[str, Any]:
        access_token = await self._access_token(connection)
        try:
            async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.TimeoutException as error:
            raise GmailRetryableError("Gmail request timed out") from error
        self._raise_for_status(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise GmailTerminalError("Gmail response was malformed")
        return payload

    async def _access_token(self, connection: GoogleConnection) -> str:
        try:
            async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
                response = await client.post(
                    _GOOGLE_TOKEN_URL,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token_reader(connection),
                        "grant_type": "refresh_token",
                    },
                )
        except httpx.TimeoutException as error:
            raise GmailRetryableError("Google token refresh timed out") from error
        self._raise_for_status(response)
        payload = response.json()
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise GmailTerminalError("Google token response was malformed")
        return access_token

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 404:
            raise GmailHistoryExpiredError("Gmail history cursor expired")
        if response.status_code in {401, 403}:
            raise GmailTerminalError(f"Gmail authorization failed: {response.status_code}")
        if response.status_code == 429 or response.status_code >= 500:
            raise GmailRetryableError(f"Gmail temporarily unavailable: {response.status_code}")
        response.raise_for_status()

    @staticmethod
    def _label_id(connection: GoogleConnection) -> str:
        if not connection.gmail_label_id:
            raise GmailTerminalError("Gmail label is not configured")
        return connection.gmail_label_id

    @staticmethod
    def _string_field(payload: dict[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value:
            raise GmailTerminalError(f"Gmail response is missing {name}")
        return value

    @staticmethod
    def _int_field(payload: dict[str, Any], name: str) -> int:
        value = payload.get(name)
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise GmailTerminalError(f"Gmail response is missing {name}") from error

    @staticmethod
    def _headers(payload: dict[str, Any]) -> dict[str, str]:
        values: dict[str, str] = {}
        headers = payload.get("headers", [])
        if not isinstance(headers, list):
            return values
        for header in headers:
            if not isinstance(header, dict):
                continue
            name, value = header.get("name"), header.get("value")
            if isinstance(name, str) and isinstance(value, str):
                values.setdefault(name.lower(), value.strip())
        return values

    @classmethod
    def _message_text(cls, payload: dict[str, Any]) -> str:
        plain_parts: list[str] = []
        html_parts: list[str] = []
        cls._collect_text_parts(payload, plain_parts, html_parts)
        if plain_parts:
            return "\n".join(part for part in plain_parts if part).strip()
        if html_parts:
            parser = _HtmlToText()
            parser.feed("\n".join(html_parts))
            text = parser.text()
            if text:
                return text
        raise UnsupportedGmailMessageError("Gmail message has no text/plain or text/html body")

    @classmethod
    def _collect_text_parts(
        cls, payload: dict[str, Any], plain_parts: list[str], html_parts: list[str]
    ) -> None:
        mime_type = payload.get("mimeType")
        if mime_type in {"text/plain", "text/html"}:
            body = payload.get("body")
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, str) and data:
                decoded = cls._decode_body(data)
                if mime_type == "text/plain":
                    plain_parts.append(decoded)
                else:
                    html_parts.append(decoded)
        parts = payload.get("parts", [])
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict):
                    cls._collect_text_parts(part, plain_parts, html_parts)

    @staticmethod
    def _decode_body(value: str) -> str:
        try:
            padded = value + "=" * (-len(value) % 4)
            return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
        except (ValueError, UnicodeError) as error:
            raise UnsupportedGmailMessageError("Gmail message body was malformed") from error
