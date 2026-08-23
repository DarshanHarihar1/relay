from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.domain.ingestion import GoogleConnection
from app.security import FieldCipher
from app.settings import GoogleOAuthSettings


GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class GoogleConnectionRequest(BaseModel):
    enable_contacts_picker: bool = False


class GoogleOAuthStore(Protocol):
    async def create_state_nonce(self, nonce: str, expires_at: datetime) -> None: ...

    async def consume_state_nonce(self, nonce: str) -> bool: ...

    async def put_connection(self, connection: GoogleConnection) -> None: ...

    async def get_connection(self, user_id: str) -> GoogleConnection | None: ...

    async def get_active_connections_by_gmail_email(
        self, email_address: str
    ) -> list[GoogleConnection]: ...

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]: ...

    async def delete_connection(self, user_id: str) -> None: ...

    async def delete_gmail_cursor(self, user_id: str) -> None: ...

    async def cancel_watch_renewal(self, user_id: str) -> None: ...

    async def remove_unreferenced_selected_contacts(self, user_id: str) -> None: ...


class InMemoryGoogleOAuthStore:
    """Test double. Deployments use FirestoreGoogleStore, which is now required."""

    def __init__(self) -> None:
        self._nonces: dict[str, datetime] = {}
        self._connections: dict[str, GoogleConnection] = {}

    async def create_state_nonce(self, nonce: str, expires_at: datetime) -> None:
        self._nonces[nonce] = expires_at

    async def consume_state_nonce(self, nonce: str) -> bool:
        expires_at = self._nonces.pop(nonce, None)
        return expires_at is not None and expires_at > datetime.now(timezone.utc)

    async def put_connection(self, connection: GoogleConnection) -> None:
        self._connections[connection.user_id] = connection

    async def get_connection(self, user_id: str) -> GoogleConnection | None:
        return self._connections.get(user_id)

    async def get_active_connections_by_gmail_email(
        self, email_address: str
    ) -> list[GoogleConnection]:
        wanted = email_address.casefold()
        return [
            connection
            for connection in self._connections.values()
            if connection.gmail_email_address
            and connection.gmail_email_address.casefold() == wanted
        ]

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]:
        return [
            connection
            for connection in self._connections.values()
            if connection.gmail_watch_expires_at is not None
            and connection.gmail_watch_expires_at <= before
        ]

    async def delete_connection(self, user_id: str) -> None:
        self._connections.pop(user_id, None)

    async def delete_gmail_cursor(self, user_id: str) -> None:
        del user_id

    async def cancel_watch_renewal(self, user_id: str) -> None:
        del user_id

    async def remove_unreferenced_selected_contacts(self, user_id: str) -> None:
        del user_id


class GoogleOAuthService:
    def __init__(
        self,
        *,
        settings: GoogleOAuthSettings,
        cipher: FieldCipher,
        store: GoogleOAuthStore,
    ) -> None:
        self._settings = settings
        self._cipher = cipher
        self._store = store

    @property
    def default_scopes(self) -> frozenset[str]:
        return frozenset({GMAIL_SCOPE, CALENDAR_READONLY_SCOPE})

    @property
    def default_scopes_string(self) -> str:
        return " ".join(sorted(self.default_scopes))

    @property
    def contacts_scopes_string(self) -> str:
        return " ".join(sorted({*self.default_scopes, CONTACTS_SCOPE}))

    async def begin_google_connection(
        self,
        user_id: str,
        request: GoogleConnectionRequest | None = None,
        *,
        enable_contacts_picker: bool | None = None,
    ) -> str:
        if not user_id:
            raise ValueError("A user ID is required")
        opt_in = enable_contacts_picker
        if opt_in is None:
            opt_in = request.enable_contacts_picker if request is not None else False
        scopes = set(self.default_scopes)
        if opt_in:
            scopes.add(CONTACTS_SCOPE)
        nonce = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._settings.state_ttl_seconds)
        await self._store.create_state_nonce(nonce, expires_at)
        state = self._sign_state(
            {
                "user_id": user_id,
                "nonce": nonce,
                "scopes": sorted(scopes),
                "return_path": "/connections/google",
                "expires_at": int(expires_at.timestamp()),
            },
        )
        query = urlencode(
            {
                "client_id": self._settings.client_id,
                "redirect_uri": self._settings.redirect_uri,
                "response_type": "code",
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "scope": " ".join(sorted(scopes)),
                "state": state,
            },
            safe=":/",
        )
        return f"{_GOOGLE_AUTHORIZE_URL}?{query}"

    async def complete_google_connection(
        self,
        *,
        code: str,
        state: str,
        token_response: Mapping[str, Any] | None = None,
    ) -> GoogleConnection:
        payload = self._verify_state(state)
        if not await self._store.consume_state_nonce(payload["nonce"]):
            raise ValueError("OAuth state is expired or has already been used")
        response = dict(token_response) if token_response is not None else await self._exchange_code(code)
        refresh_token = response.get("refresh_token")
        scope = response.get("scope")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ValueError("Google did not return a refresh token")
        if not isinstance(scope, str):
            raise ValueError("Google did not return granted scopes")
        granted_scopes = frozenset(scope.split())
        expected_scopes = frozenset(payload["scopes"])
        if granted_scopes != expected_scopes:
            raise ValueError("Google returned scopes that do not match the signed request")
        connection = GoogleConnection(
            user_id=payload["user_id"],
            granted_scopes=granted_scopes,
            gmail_label_id=self._settings.gmail_label_id,
            encrypted_refresh_token=self._cipher.encrypt(refresh_token),
            connected_at=datetime.now(timezone.utc),
            contacts_picker_enabled=CONTACTS_SCOPE in granted_scopes,
        )
        await self._store.put_connection(connection)
        return connection

    async def disconnect_google(self, user_id: str) -> None:
        connection = await self._store.get_connection(user_id)
        if connection is not None:
            await self._revoke_token(self._cipher.decrypt(connection.encrypted_refresh_token))
        await self._store.delete_gmail_cursor(user_id)
        await self._store.cancel_watch_renewal(user_id)
        await self._store.delete_connection(user_id)
        await self._store.remove_unreferenced_selected_contacts(user_id)

    async def get_connection(self, user_id: str) -> GoogleConnection | None:
        """Return the requesting user's existing server-side connection only."""
        return await self._store.get_connection(user_id)

    async def put_connection(self, connection: GoogleConnection) -> None:
        await self._store.put_connection(connection)

    async def get_active_connections_by_gmail_email(
        self, email_address: str
    ) -> list[GoogleConnection]:
        """Resolve a Gmail push mailbox to connections. The caller requires exactly one."""
        return await self._store.get_active_connections_by_gmail_email(email_address)

    async def list_connections_due_for_watch_renewal(
        self, before: datetime
    ) -> list[GoogleConnection]:
        return await self._store.list_connections_due_for_watch_renewal(before)

    def decrypt_refresh_token(self, connection: GoogleConnection) -> str:
        return self._cipher.decrypt(connection.encrypted_refresh_token)

    def _sign_state(self, payload: Mapping[str, Any]) -> str:
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        signature = hmac.new(
            self._settings.state_signing_key.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        return f"{encoded}.{signature}"

    def _verify_state(self, state: str) -> dict[str, Any]:
        try:
            encoded, received_signature = state.rsplit(".", 1)
        except ValueError as error:
            raise ValueError("Invalid OAuth state") from error
        expected_signature = hmac.new(
            self._settings.state_signing_key.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(received_signature, expected_signature):
            raise ValueError("Invalid OAuth state signature")
        try:
            payload = json.loads(base64.urlsafe_b64decode(encoded.encode()))
        except (ValueError, json.JSONDecodeError) as error:
            raise ValueError("Invalid OAuth state") from error
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(name), str) for name in ("user_id", "nonce", "return_path")
        ):
            raise ValueError("Invalid OAuth state")
        scopes = payload.get("scopes")
        expires_at = payload.get("expires_at")
        if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
            raise ValueError("Invalid OAuth state scopes")
        if frozenset(scopes) not in (self.default_scopes, frozenset({*self.default_scopes, CONTACTS_SCOPE})):
            raise ValueError("Invalid OAuth state scopes")
        if not isinstance(expires_at, int) or expires_at <= int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("OAuth state has expired")
        return payload

    async def _exchange_code(self, code: str) -> dict[str, Any]:
        if not code:
            raise ValueError("Missing authorization code")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "redirect_uri": self._settings.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid Google token response")
        return data

    async def _revoke_token(self, refresh_token: str) -> None:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(_GOOGLE_REVOKE_URL, data={"token": refresh_token})
        if response.status_code not in {200, 400}:
            response.raise_for_status()
