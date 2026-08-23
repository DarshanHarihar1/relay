from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.contracts import AwareDatetime, ContractModel, NonEmptyString


class GoogleConnection(ContractModel):
    """The minimum persisted state for one user's Google connection."""

    user_id: NonEmptyString
    provider: Literal["google"] = "google"
    granted_scopes: frozenset[NonEmptyString]
    gmail_label_id: NonEmptyString | None = None
    # This mailbox is used only to resolve an authenticated Gmail notification
    # to exactly one connected Relay user. It is never supplied by Pub/Sub as a user ID.
    gmail_email_address: NonEmptyString | None = None
    gmail_history_id: int | None = Field(default=None, ge=0)
    gmail_watch_expires_at: AwareDatetime | None = None
    encrypted_refresh_token: NonEmptyString
    connected_at: AwareDatetime
    contacts_picker_enabled: bool = False


class GmailNotification(ContractModel):
    email_address: NonEmptyString
    history_id: int = Field(ge=0)
    published_at: AwareDatetime


class GmailProfile(ContractModel):
    """The mailbox identity Relay resolves server-side, never from a push payload."""

    email_address: NonEmptyString
    history_id: int = Field(ge=0)


class GmailMessage(ContractModel):
    """Sanitized Gmail message content eligible for bounded extraction."""

    id: NonEmptyString
    thread_id: NonEmptyString
    history_id: int = Field(ge=0)
    internal_date: AwareDatetime
    label_ids: frozenset[NonEmptyString]
    subject: str
    from_address: NonEmptyString
    text_body: str


class WatchRegistration(ContractModel):
    history_id: int = Field(ge=0)
    expires_at: AwareDatetime
    request: dict[str, Any] = Field(default_factory=dict)


class HistoryPage(ContractModel):
    history_id: int = Field(ge=0)
    added_message_ids: list[NonEmptyString] = Field(default_factory=list)
    next_page_token: NonEmptyString | None = None


class DisruptionCandidate(ContractModel):
    change_type: Literal["flight_delay", "flight_cancelled", "schedule_change", "other"]
    provider: str | None
    booking_reference: str | None
    old_time: AwareDatetime | None
    new_time: AwareDatetime | None
    location_text: str | None
    confidence: float = Field(ge=0, le=1)
    evidence_excerpt: str = Field(max_length=500)


class MatchResult(ContractModel):
    status: Literal["matched", "needs_review", "no_match"]
    commitment_id: NonEmptyString | None = None
    score: int = Field(ge=0)
    reasons: list[NonEmptyString]

    @model_validator(mode="after")
    def matched_result_has_commitment(self) -> "MatchResult":
        if self.status == "matched" and self.commitment_id is None:
            raise ValueError("A matched result requires a commitment_id")
        if self.status != "matched" and self.commitment_id is not None:
            raise ValueError("Only a matched result can include a commitment_id")
        return self


class SelectedContact(ContractModel):
    """A user's one chosen pickup contact, not a contact-directory record."""

    display_name: str = Field(min_length=1, max_length=200)
    encrypted_phone_number: NonEmptyString
    phone_last4: str = Field(pattern=r"^\d{4}$")
    source: Literal["google_picker", "manual"]
    selected_at: AwareDatetime
