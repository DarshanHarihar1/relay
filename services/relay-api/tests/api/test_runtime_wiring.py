"""The dependency factories build the real object graph used in Cloud Run.

Every other test overrides these, so without this file a wrong constructor
argument would only surface as a 500 in production.
"""

from __future__ import annotations

import pytest

from app.security import FernetFieldCipher


REQUIRED_ENV = {
    "GOOGLE_CLOUD_PROJECT": "relay-test",
    "GOOGLE_OAUTH_CLIENT_ID": "client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://relay.example/v1/google/callback",
    "GOOGLE_GMAIL_LABEL_ID": "Label_123",
    "GOOGLE_GMAIL_TOPIC": "projects/relay-test/topics/gmail-events",
    "GOOGLE_PUBSUB_PUSH_AUDIENCE": "https://relay.example/v1/events/gmail",
    "GOOGLE_OAUTH_STATE_SIGNING_KEY": "state-signing-key",
    "GOOGLE_PUBSUB_PUSH_SERVICE_ACCOUNT": "relay-api-sa@relay-test.iam.gserviceaccount.com",
    "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8080",
}


@pytest.fixture
def configured(monkeypatch):
    from app.routes import google as google_routes
    from app.routes import pubsub as pubsub_routes

    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("APP_ENCRYPTION_KEY", FernetFieldCipher.generate_key().decode())
    # ADC belongs to the deployment, not to a wiring assertion.
    monkeypatch.setattr(
        "app.adapters.gemini.default_access_token_provider", lambda: (lambda: "token")
    )
    for factory in (
        google_routes.get_google_oauth_service,
        google_routes.get_gmail_watch_service,
        google_routes.get_contact_selection_service,
        pubsub_routes.get_gmail_pubsub_handler,
        pubsub_routes.get_maintenance_handler,
    ):
        factory.cache_clear()
    yield
    for factory in (
        google_routes.get_google_oauth_service,
        google_routes.get_gmail_watch_service,
        google_routes.get_contact_selection_service,
        pubsub_routes.get_gmail_pubsub_handler,
        pubsub_routes.get_maintenance_handler,
    ):
        factory.cache_clear()


def test_the_gmail_push_handler_graph_builds_from_environment(configured) -> None:
    from app.adapters.gemini import VertexGeminiExtractor
    from app.routes.pubsub import GmailPubSubHandler, get_gmail_pubsub_handler
    from app.services.commitment_matcher import ConservativeCommitmentMatcher

    handler = get_gmail_pubsub_handler()

    assert isinstance(handler, GmailPubSubHandler)
    ingestion = handler._queue._worker._ingestion
    assert isinstance(ingestion._extractor, VertexGeminiExtractor)
    assert isinstance(ingestion._matcher, ConservativeCommitmentMatcher)
    assert ingestion._phase3 is None, "Phase 3 handoff has no durable queue wired yet"


def test_the_maintenance_handler_graph_builds_from_environment(configured) -> None:
    from app.repositories.retention import FirestoreRetentionStore
    from app.routes.pubsub import MaintenanceHandler, get_maintenance_handler
    from app.services.gmail_ingestion import GmailWatchService

    handler = get_maintenance_handler()

    assert isinstance(handler, MaintenanceHandler)
    assert isinstance(handler._maintenance._retention._store, FirestoreRetentionStore)
    assert isinstance(handler._maintenance._watches, GmailWatchService)


def test_the_google_oauth_and_picker_graphs_build_from_environment(configured) -> None:
    from app.routes.google import (
        get_contact_selection_service,
        get_gmail_watch_service,
        get_google_oauth_service,
    )
    from app.services.contact_selection import ContactSelectionService
    from app.services.gmail_ingestion import GmailWatchService

    assert get_google_oauth_service() is not None
    assert isinstance(get_gmail_watch_service(), GmailWatchService)
    assert isinstance(get_contact_selection_service(), ContactSelectionService)


@pytest.mark.parametrize(
    "missing",
    ["GOOGLE_CLOUD_PROJECT", "APP_ENCRYPTION_KEY", "GOOGLE_PUBSUB_PUSH_SERVICE_ACCOUNT"],
)
def test_missing_configuration_names_the_variable_instead_of_crashing_obscurely(
    configured, monkeypatch, missing
) -> None:
    from app.routes import google as google_routes
    from app.routes import pubsub as pubsub_routes

    monkeypatch.delenv(missing, raising=False)
    google_routes.get_google_oauth_service.cache_clear()
    pubsub_routes.get_gmail_pubsub_handler.cache_clear()

    with pytest.raises(RuntimeError) as error:
        pubsub_routes.get_gmail_pubsub_handler()

    assert missing in str(error.value)
