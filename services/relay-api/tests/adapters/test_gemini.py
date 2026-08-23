from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.domain.ingestion import GmailMessage


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
BODY = (
    "Your flight AI202 is delayed. New departure time is 2026-08-24T14:30:00+05:30. "
    "Booking reference AB12CD."
)


def _message(text_body: str = BODY) -> GmailMessage:
    return GmailMessage(
        id="m1",
        thread_id="t1",
        history_id=900,
        internal_date=NOW,
        label_ids=frozenset({"Label_123"}),
        subject="Flight AI202 delayed",
        from_address="updates@example.test",
        text_body=text_body,
    )


def _candidate_json(**overrides) -> str:
    payload = {
        "change_type": "flight_delay",
        "provider": "Example Air",
        "booking_reference": "AB12CD",
        "old_time": "2026-08-24T09:00:00+05:30",
        "new_time": "2026-08-24T14:30:00+05:30",
        "location_text": "BLR",
        "confidence": 0.92,
        "evidence_excerpt": "Your flight AI202 is delayed.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _extractor(handler, **kwargs):
    from app.adapters.gemini import VertexGeminiExtractor

    return VertexGeminiExtractor(
        project="relay",
        location="us-central1",
        model="gemini-2.5-flash",
        access_token_provider=lambda: "ya29.token",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _responds(text: str, status: int = 200, payload: dict | None = None):
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = json.loads(request.content) if request.content else {}
        if payload is not None:
            return httpx.Response(status, json=payload)
        return httpx.Response(
            status,
            json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]},
        )

    return handler, captured


@pytest.mark.asyncio
async def test_extractor_uses_schema_and_a_minimal_message() -> None:
    from app.adapters.gemini import EXTRACTION_SCHEMA

    handler, captured = _responds(_candidate_json())
    extractor = _extractor(handler)

    candidate = await extractor.extract(message=_message(), correlation_id="corr1")

    body = captured["body"]
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == EXTRACTION_SCHEMA
    assert "tools" not in body
    assert json.loads(body["contents"][0]["parts"][0]["text"]) == {
        "subject": "Flight AI202 delayed",
        "from": "updates@example.test",
        "body": BODY,
    }
    assert candidate.change_type == "flight_delay"
    assert candidate.confidence == 0.92


@pytest.mark.asyncio
async def test_body_is_truncated_to_twelve_thousand_characters() -> None:
    long_body = "Your flight AI202 is delayed. " + ("x" * 20000)
    handler, captured = _responds(_candidate_json())

    await _extractor(handler).extract(message=_message(long_body), correlation_id="corr1")

    sent = json.loads(captured["body"]["contents"][0]["parts"][0]["text"])
    assert len(sent["body"]) == 12000
    assert sent["body"] == long_body[:12000]


@pytest.mark.asyncio
async def test_schema_invalid_output_requires_review_without_retrying() -> None:
    from app.adapters.gemini import ExtractionReviewRequired

    handler, _ = _responds('{"change_type": "not_a_change_type"}')

    with pytest.raises(ExtractionReviewRequired) as error:
        await _extractor(handler).extract(message=_message(), correlation_id="corr1")

    assert error.value.reason == "SCHEMA_INVALID"


@pytest.mark.asyncio
async def test_low_confidence_requires_review() -> None:
    from app.adapters.gemini import ExtractionReviewRequired

    handler, _ = _responds(_candidate_json(confidence=0.74))

    with pytest.raises(ExtractionReviewRequired) as error:
        await _extractor(handler).extract(message=_message(), correlation_id="corr1")

    assert error.value.reason == "LOW_CONFIDENCE"


@pytest.mark.asyncio
async def test_delay_without_a_new_time_requires_review() -> None:
    from app.adapters.gemini import ExtractionReviewRequired

    handler, _ = _responds(_candidate_json(new_time=None))

    with pytest.raises(ExtractionReviewRequired) as error:
        await _extractor(handler).extract(message=_message(), correlation_id="corr1")

    assert error.value.reason == "MISSING_NEW_TIME"


@pytest.mark.asyncio
async def test_invented_evidence_requires_review() -> None:
    from app.adapters.gemini import ExtractionReviewRequired

    handler, _ = _responds(_candidate_json(evidence_excerpt="Your flight was cancelled outright."))

    with pytest.raises(ExtractionReviewRequired) as error:
        await _extractor(handler).extract(message=_message(), correlation_id="corr1")

    assert error.value.reason == "EVIDENCE_NOT_IN_MESSAGE"


@pytest.mark.asyncio
async def test_safety_block_requires_review() -> None:
    from app.adapters.gemini import ExtractionReviewRequired

    handler, _ = _responds("", payload={"promptFeedback": {"blockReason": "SAFETY"}})

    with pytest.raises(ExtractionReviewRequired) as error:
        await _extractor(handler).extract(message=_message(), correlation_id="corr1")

    assert error.value.reason == "SAFETY_BLOCKED"


@pytest.mark.asyncio
async def test_provider_overload_is_retryable_not_review() -> None:
    from app.adapters.errors import RetryableProviderError

    handler, _ = _responds("", status=503, payload={"error": "unavailable"})

    with pytest.raises(RetryableProviderError):
        await _extractor(handler).extract(message=_message(), correlation_id="corr1")


@pytest.mark.asyncio
async def test_the_model_is_never_offered_tools_and_runs_at_zero_temperature() -> None:
    handler, captured = _responds(_candidate_json())

    await _extractor(handler).extract(message=_message(), correlation_id="corr1")

    assert captured["body"]["generationConfig"]["temperature"] == 0
    assert "tools" not in captured["body"]
    assert "toolConfig" not in captured["body"]
    assert captured["request"].url.path.endswith(
        "/publishers/google/models/gemini-2.5-flash:generateContent"
    )
