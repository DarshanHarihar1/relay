from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from app.domain.ingestion import GoogleConnection
from app.security import FernetFieldCipher


@pytest.fixture
def oauth_settings():
    from app.settings import GoogleOAuthSettings

    return GoogleOAuthSettings(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://relay.example/v1/google/callback",
        gmail_label_id="Label_123",
        gmail_topic="projects/relay/topics/gmail-events",
        pubsub_push_audience="https://relay.example/v1/events/gmail",
        state_signing_key="test-state-signing-key",
    )


@pytest.mark.asyncio
async def test_default_connection_requests_only_gmail_and_calendar(oauth_settings):
    from app.adapters.google_auth import (
    CALENDAR_EVENTS_SCOPE,
        CONTACTS_SCOPE,
        GMAIL_SCOPE,
        GoogleOAuthService,
        InMemoryGoogleOAuthStore,
    )

    service = GoogleOAuthService(
        settings=oauth_settings,
        cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
        store=InMemoryGoogleOAuthStore(),
    )

    authorization_url = await service.begin_google_connection("user-1", enable_contacts_picker=False)

    assert GMAIL_SCOPE in authorization_url
    assert CALENDAR_EVENTS_SCOPE in authorization_url
    assert CONTACTS_SCOPE not in authorization_url


@pytest.mark.asyncio
async def test_completion_rejects_contacts_scope_not_selected_in_signed_state(oauth_settings):
    from app.adapters.google_auth import (
        CONTACTS_SCOPE,
        GoogleOAuthService,
        InMemoryGoogleOAuthStore,
    )

    service = GoogleOAuthService(
        settings=oauth_settings,
        cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
        store=InMemoryGoogleOAuthStore(),
    )
    authorization_url = await service.begin_google_connection("user-1", enable_contacts_picker=False)
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    with pytest.raises(ValueError, match="returned scopes"):
        await service.complete_google_connection(
            code="authorization-code",
            state=state,
            token_response={
                "refresh_token": "refresh-token",
                "scope": f"{service.default_scopes_string} {CONTACTS_SCOPE}",
            },
        )


@pytest.mark.asyncio
async def test_completion_encrypts_refresh_token_before_storing(oauth_settings):
    from app.adapters.google_auth import GoogleOAuthService, InMemoryGoogleOAuthStore

    service = GoogleOAuthService(
        settings=oauth_settings,
        cipher=FernetFieldCipher(FernetFieldCipher.generate_key()),
        store=InMemoryGoogleOAuthStore(),
    )
    authorization_url = await service.begin_google_connection("user-1", enable_contacts_picker=True)
    state = parse_qs(urlparse(authorization_url).query)["state"][0]

    connection = await service.complete_google_connection(
        code="authorization-code",
        state=state,
        token_response={"refresh_token": "refresh-token", "scope": service.contacts_scopes_string},
    )

    assert connection.user_id == "user-1"
    assert connection.encrypted_refresh_token != "refresh-token"
    assert service.decrypt_refresh_token(connection) == "refresh-token"


def test_google_connection_fixture_is_timezone_aware():
    connection = GoogleConnection(
        user_id="user-1",
        granted_scopes=frozenset({"scope"}),
        encrypted_refresh_token="enc:v1:token",
        connected_at=datetime.now(timezone.utc),
    )

    assert connection.provider == "google"
