from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from app.adapters.errors import RetryableProviderError, TerminalProviderError
from app.domain.ingestion import DisruptionCandidate, GmailMessage


logger = logging.getLogger("relay.extraction")

# The schema the model must fill. It is derived from the persisted contract so
# the model can never return a shape the domain does not already accept.
EXTRACTION_SCHEMA: dict[str, Any] = DisruptionCandidate.model_json_schema()

MAX_BODY_CHARACTERS = 12000
MINIMUM_CONFIDENCE = 0.75

_BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"})

INSTRUCTION = (
    "You read one email and report only what it literally states about a change to a "
    "travel or reservation commitment.\n"
    "Rules:\n"
    "1. Extract only the named schema fields. Add nothing else.\n"
    "2. Never infer, guess, or compute a fact the email does not state. Use null for "
    "anything the email does not state.\n"
    "3. evidence_excerpt must be copied verbatim from the supplied body, at most 500 "
    "characters. Never paraphrase it.\n"
    "4. confidence reflects how directly the email states the change, not how likely the "
    "change is.\n"
    "5. If the email is not about a changed commitment, use change_type \"other\" with a "
    "low confidence.\n"
    "You have no tools and take no actions."
)


class ExtractionReviewRequired(Exception):
    """The model output cannot be trusted, so a human review is required.

    This is never retried: the same message would produce the same rejection.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VertexGeminiExtractor:
    """Vertex AI Gemini extraction over Google Cloud credentials.

    Only the subject, sender, and a truncated plain-text body are sent. Recipients,
    attachments, and full headers never leave the process.
    """

    def __init__(
        self,
        *,
        project: str,
        location: str,
        model: str,
        access_token_provider: Callable[[], str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._project = project
        self._location = location
        self._model = model
        self._access_token_provider = access_token_provider
        self._transport = transport
        self._timeout = timeout

    @property
    def model_version(self) -> str:
        return self._model

    async def extract(self, *, message: GmailMessage, correlation_id: str) -> DisruptionCandidate:
        body = message.text_body[:MAX_BODY_CHARACTERS]
        payload = await self._generate(
            {
                "systemInstruction": {"parts": [{"text": INSTRUCTION}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "subject": message.subject,
                                        "from": message.from_address,
                                        "body": body,
                                    }
                                )
                            }
                        ],
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": EXTRACTION_SCHEMA,
                    "temperature": 0,
                },
            }
        )
        candidate = self._validate(self._text(payload))
        self._reject_untrustworthy(candidate, body)
        # Only the outcome is logged. No subject, sender, body, or excerpt.
        logger.info(
            "extraction_candidate",
            extra={"correlation_id": correlation_id, "change_type": candidate.change_type},
        )
        return candidate

    async def _generate(self, request: dict[str, Any]) -> dict[str, Any]:
        url = (
            f"https://{self._location}-aiplatform.googleapis.com/v1/projects/{self._project}"
            f"/locations/{self._location}/publishers/google/models/{self._model}:generateContent"
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    url,
                    json=request,
                    headers={"Authorization": f"Bearer {self._access_token_provider()}"},
                )
        except httpx.TimeoutException as error:
            raise RetryableProviderError("Vertex Gemini request timed out") from error
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableProviderError(f"Vertex Gemini unavailable: {response.status_code}")
        if response.status_code >= 400:
            raise TerminalProviderError(f"Vertex Gemini rejected the request: {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise TerminalProviderError("Vertex Gemini response was malformed")
        return payload

    @staticmethod
    def _text(payload: dict[str, Any]) -> str:
        prompt_feedback = payload.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            raise ExtractionReviewRequired("SAFETY_BLOCKED")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ExtractionReviewRequired("EMPTY_RESPONSE")
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise ExtractionReviewRequired("EMPTY_RESPONSE")
        if candidate.get("finishReason") in _BLOCKED_FINISH_REASONS:
            raise ExtractionReviewRequired("SAFETY_BLOCKED")
        parts = (candidate.get("content") or {}).get("parts")
        if not isinstance(parts, list) or not parts:
            raise ExtractionReviewRequired("EMPTY_RESPONSE")
        text = parts[0].get("text") if isinstance(parts[0], dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ExtractionReviewRequired("EMPTY_RESPONSE")
        return text

    @staticmethod
    def _validate(text: str) -> DisruptionCandidate:
        # The JSON MIME type is a request, not a guarantee. Validate it again here.
        try:
            return DisruptionCandidate.model_validate_json(text)
        except (ValidationError, ValueError) as error:
            raise ExtractionReviewRequired("SCHEMA_INVALID") from error

    @staticmethod
    def _reject_untrustworthy(candidate: DisruptionCandidate, body: str) -> None:
        if not _is_quoted_from(candidate.evidence_excerpt, body):
            raise ExtractionReviewRequired("EVIDENCE_NOT_IN_MESSAGE")
        if candidate.change_type in {"flight_delay", "schedule_change"} and candidate.new_time is None:
            raise ExtractionReviewRequired("MISSING_NEW_TIME")
        if candidate.confidence < MINIMUM_CONFIDENCE:
            raise ExtractionReviewRequired("LOW_CONFIDENCE")


def _is_quoted_from(excerpt: str, body: str) -> bool:
    """Evidence must be a real quote, ignoring only whitespace differences."""
    normalized_excerpt = re.sub(r"\s+", " ", excerpt).strip()
    return bool(normalized_excerpt) and normalized_excerpt in re.sub(r"\s+", " ", body)


def default_access_token_provider() -> Callable[[], str]:
    """Application Default Credentials. No Gemini API key is ever used."""
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    def provide() -> str:
        if not credentials.valid:
            credentials.refresh(google.auth.transport.requests.Request())
        return credentials.token

    return provide


__all__ = [
    "EXTRACTION_SCHEMA",
    "ExtractionReviewRequired",
    "VertexGeminiExtractor",
    "default_access_token_provider",
]
