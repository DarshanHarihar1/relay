from __future__ import annotations

from fastapi.testclient import TestClient

from app.adapters.google_auth import CALENDAR_READONLY_SCOPE, CONTACTS_SCOPE, GMAIL_SCOPE
from app.main import app


class _AsyncTokenResponse:
    def __init__(self, service):
        self._service = service

    async def __call__(self, code):
        return {
            "refresh_token": "refresh-token",
            "scope": self._service.default_scopes_string,
        }
from app.security import FernetFieldCipher
from app.settings import GoogleOAuthSettings


def _auth_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "relay-test")
    monkeypatch.setattr(
        "app.auth._verify_firebase_id_token",
        lambda token, project_id: {"aud": project_id, "uid": "user-1", "email": "user@example.com"},
    )
    return {"Authorization": "Bearer test-token"}


def _oauth_environment(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://relay.example/v1/google/callback")
    monkeypatch.setenv("GOOGLE_GMAIL_LABEL_ID", "Label_123")
    monkeypatch.setenv("GOOGLE_GMAIL_TOPIC", "projects/relay/topics/gmail-events")
    monkeypatch.setenv("GOOGLE_PUBSUB_PUSH_AUDIENCE", "https://relay.example/v1/events/gmail")
    monkeypatch.setenv("GOOGLE_OAUTH_STATE_SIGNING_KEY", "test-state-signing-key")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", "XfPCnrnILPHCrhTRsNw4eBrlwpVVn6XofltfwSkNRk8=")


def _use_in_memory_oauth_service():
    """Scope selection is the subject here, so persistence stays out of the way."""
    from app.adapters.google_auth import GoogleOAuthService, InMemoryGoogleOAuthStore
    from app.routes.google import get_google_oauth_service

    service = GoogleOAuthService(
        settings=GoogleOAuthSettings.from_env(),
        cipher=FernetFieldCipher("XfPCnrnILPHCrhTRsNw4eBrlwpVVn6XofltfwSkNRk8="),
        store=InMemoryGoogleOAuthStore(),
    )
    app.dependency_overrides[get_google_oauth_service] = lambda: service


def test_default_connect_omits_contacts_scope(monkeypatch):
    _oauth_environment(monkeypatch)
    auth_headers = _auth_headers(monkeypatch)
    _use_in_memory_oauth_service()

    try:
        response = TestClient(app).get(
            "/v1/google/connect", headers=auth_headers, follow_redirects=False
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 307
    assert GMAIL_SCOPE in response.headers["location"]
    assert CALENDAR_READONLY_SCOPE in response.headers["location"]
    assert CONTACTS_SCOPE not in response.headers["location"]


def test_picker_opt_in_discloses_and_requests_contacts_scope(monkeypatch):
    _oauth_environment(monkeypatch)
    auth_headers = _auth_headers(monkeypatch)
    _use_in_memory_oauth_service()

    try:
        response = TestClient(app).get(
            "/v1/google/connect?enable_contacts_picker=true",
            headers=auth_headers,
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 307
    assert CONTACTS_SCOPE in response.headers["location"]
    assert "choose one person" in response.text


def test_the_callback_route_redirects_after_a_successful_connection(monkeypatch):
    """The success path of the actual HTTP route, not just the service beneath it.

    A prior regression here (RedirectResponse(location=...) instead of url=...)
    was never caught because every other test called GoogleOAuthService directly.
    """
    from urllib.parse import parse_qs, urlparse

    from app.adapters.google_auth import GoogleOAuthService, InMemoryGoogleOAuthStore
    from app.routes.google import get_gmail_watch_service, get_google_oauth_service

    _oauth_environment(monkeypatch)
    store = InMemoryGoogleOAuthStore()
    service = GoogleOAuthService(
        settings=GoogleOAuthSettings.from_env(),
        cipher=FernetFieldCipher("XfPCnrnILPHCrhTRsNw4eBrlwpVVn6XofltfwSkNRk8="),
        store=store,
    )
    monkeypatch.setattr(
        service,
        "_exchange_code",
        _AsyncTokenResponse(service),
    )

    class NoOpWatches:
        async def register_gmail_watch(self, user_id):
            return None

    app.dependency_overrides[get_google_oauth_service] = lambda: service
    app.dependency_overrides[get_gmail_watch_service] = lambda: NoOpWatches()
    try:
        begin = TestClient(app).get(
            "/v1/google/connect",
            headers=_auth_headers(monkeypatch),
            follow_redirects=False,
        )
        state = parse_qs(urlparse(begin.headers["location"]).query)["state"][0]

        response = TestClient(app).get(
            "/v1/google/callback",
            params={"code": "authorization-code", "state": state},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 303
    assert response.headers["location"] == "/connections/google"
