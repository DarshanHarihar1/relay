import logging

from app.observability import RelayLogEvent, classify_exception, log_event, record_action_metric
from app.providers.webhooks import WebhookVerificationError


EVENT = RelayLogEvent(
    event="action_transition",
    severity="INFO",
    correlation_id="corr_1",
    user_id_hash="user_hash",
    action_id="act_1",
    approval_id=None,
    provider="vapi",
    outcome="verified",
    latency_ms=42,
)


def test_log_event_redacts_nested_provider_and_phone_data(caplog):
    caplog.set_level(logging.INFO, logger="relay.observability")

    log_event(
        EVENT,
        request={
            "authorization": "Bearer x",
            "phone": "+919999999999",
            "provider_ref": "call_abc",
        },
    )

    assert "999999999" not in caplog.text
    assert "call_abc" not in caplog.text
    assert "Bearer x" not in caplog.text
    assert '"correlation_id":"corr_1"' in caplog.text


def test_exception_classifier_marks_bad_webhook_signature_as_security():
    assert classify_exception(WebhookVerificationError()) == "security"


def test_action_metric_records_only_opaque_dimensions(caplog):
    caplog.set_level(logging.INFO, logger="relay.metrics")

    record_action_metric(
        action_type="voice_call",
        state="verified",
        provider="vapi",
        latency_ms=120,
    )

    assert "voice_call" in caplog.text
    assert "+919999999999" not in caplog.text
