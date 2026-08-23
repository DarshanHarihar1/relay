from __future__ import annotations

from datetime import date as Date, datetime, time as Time, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import TypeAliasType


JsonValue = TypeAliasType(
    "JsonValue",
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"],
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionState(str, Enum):
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    AUTHORIZED = "authorized"
    DISPATCHED = "dispatched"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    NEEDS_USER = "needs_user"
    RETRYABLE_FAILURE = "retryable_failure"
    FAILED = "failed"
    VERIFIED = "verified"
    HANDOFF_OPENED = "handoff_opened"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    DECLINE = "decline"


class ProviderKind(str, Enum):
    VAPI = "vapi"
    TWILIO = "twilio"
    GOOGLE_CALENDAR = "calendar"
    UBER = "uber"


class CallGoal(str, Enum):
    CONFIRM_FRIEND_PICKUP = "confirm_friend_pickup"
    RESCHEDULE_RESTAURANT_RESERVATION = "reschedule_restaurant_reservation"
    CONFIRM_HOTEL_TIMING = "confirm_hotel_timing"


class CallOutcomeKind(str, Enum):
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    NO_ANSWER = "no_answer"
    VOICEMAIL = "voicemail"
    TRANSFER_REQUESTED = "transfer_requested"
    CONTRADICTION = "contradiction"
    UNEXPECTED_FEE = "unexpected_fee"
    PROVIDER_ERROR = "provider_error"


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime values must include a timezone offset")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_aware_datetime)]
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class VoiceCallAuthorizationSnapshot(ContractModel):
    type: Literal["voice_call"]
    goal: NonEmptyString
    recipient_ref: NonEmptyString
    identity_disclosure: NonEmptyString
    authorized_options: list[NonEmptyString] = Field(min_length=1)
    max_fee_inr: int = Field(ge=0, le=100000)
    must_not: list[NonEmptyString]
    required_evidence: list[NonEmptyString] = Field(min_length=1)
    expires_at: AwareDatetime


class CalendarHoldAuthorizationSnapshot(ContractModel):
    type: Literal["calendar_hold"]
    calendar_id: NonEmptyString
    start_at: AwareDatetime
    end_at: AwareDatetime
    visibility: Literal["private"]

    @model_validator(mode="after")
    def validate_interval(self) -> "CalendarHoldAuthorizationSnapshot":
        if self.end_at <= self.start_at:
            raise ValueError("Calendar hold end_at must be after start_at")
        return self


class UberDeepLinkAuthorizationSnapshot(ContractModel):
    type: Literal["uber_deep_link"]
    pickup: NonEmptyString
    destination: NonEmptyString
    handoff_label: Literal["Open Uber"]


AuthorizationSnapshot = Annotated[
    VoiceCallAuthorizationSnapshot | CalendarHoldAuthorizationSnapshot | UberDeepLinkAuthorizationSnapshot,
    Field(discriminator="type"),
]


class ActionRecord(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    repair_plan_id: NonEmptyString
    repair_plan_version: int = Field(ge=1)
    type: Literal["voice_call", "calendar_hold", "uber_deep_link"]
    target_ref: NonEmptyString
    idempotency_key: NonEmptyString
    authorization_snapshot: AuthorizationSnapshot
    provider_ref: NonEmptyString | None = None
    state: ActionState
    retry_count: int = Field(default=0, ge=0)
    verification_evidence: dict[str, JsonValue] | None = None
    correlation_id: NonEmptyString
    expires_at: AwareDatetime | None = None
    dispatched_at: AwareDatetime | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_snapshot_type(self) -> "ActionRecord":
        if self.type != self.authorization_snapshot.type:
            raise ValueError("Authorization snapshot type must match action type")
        if self.state is ActionState.HANDOFF_OPENED and self.type != "uber_deep_link":
            raise ValueError("Only an Uber deep link action can enter handoff_opened")
        return self


class Approval(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    action_ids: list[NonEmptyString] = Field(min_length=1)
    state: Literal["awaiting_approval", "approved", "declined"]
    version: int = Field(ge=1)
    correlation_id: NonEmptyString
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Phase 3 extension. A repair-plan approval batch expires; a Phase 1
    # approval created before this field existed simply has no expiry.
    expires_at: AwareDatetime | None = None


class ApprovalDecisionRequest(ContractModel):
    approval_id: NonEmptyString
    decision: Literal["approve", "decline"]
    expected_version: int = Field(ge=1)


class ApprovalDecisionResponse(ContractModel):
    approval_id: NonEmptyString
    state: Literal["approved", "declined"]
    action_ids: list[NonEmptyString]


class ActionDispatchRecord(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    action_id: NonEmptyString
    status: Literal["pending", "claimed", "completed"]
    correlation_id: NonEmptyString
    attempts: int = Field(default=0, ge=0)
    provider_ref: NonEmptyString | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)


class CallContract(ContractModel):
    action_id: NonEmptyString
    # Phase 3 stores this as a bounded descriptive string rather than one of
    # the plan's three example goals. Preserve the persisted value verbatim.
    goal: NonEmptyString
    recipient_ref: NonEmptyString
    identity_disclosure: NonEmptyString
    authorized_options: list[NonEmptyString] = Field(min_length=1, max_length=3)
    max_fee_inr: Literal[0]
    must_not: set[NonEmptyString]
    required_evidence: set[NonEmptyString]
    expires_at: AwareDatetime


class RecordCallOutcomeInput(ContractModel):
    action_id: NonEmptyString
    outcome: CallOutcomeKind
    venue: NonEmptyString | None = None
    date: Date | None = None
    party_size: int | None = Field(default=None, ge=1, le=20)
    confirmed_time: Time | None = None
    fee_inr: Decimal | None = Field(default=None, ge=0)
    requested_transfer: bool = False
    redacted_excerpt: str | None = Field(default=None, max_length=280)


class OutcomeValidation(ContractModel):
    state: ActionState
    reason: NonEmptyString
    missing_evidence: list[NonEmptyString] = Field(default_factory=list)


class ActionStatusResponse(ContractModel):
    action_id: NonEmptyString
    state: ActionState
    retry_count: int = Field(ge=0)
    verification_evidence: dict[str, JsonValue] | None = None
    correlation_id: NonEmptyString


class HandoffResponse(ContractModel):
    action_id: NonEmptyString
    state: Literal["handoff_opened"]
    url: NonEmptyString


def _outcome_reason(kind: CallOutcomeKind) -> str:
    return f"outcome_{kind.value}"


def _authorized_times(options: set[str]) -> set[Time]:
    parsed: set[Time] = set()
    for option in options:
        try:
            parsed.add(Time.fromisoformat(option))
        except ValueError:
            continue
    return parsed


def validate_call_outcome(
    contract: CallContract,
    outcome: RecordCallOutcomeInput,
    *,
    now: datetime | None = None,
) -> OutcomeValidation:
    """Validate a structured voice result against the immutable call bounds."""
    check_at = now or datetime.now(timezone.utc)
    if outcome.action_id != contract.action_id:
        return OutcomeValidation(state=ActionState.NEEDS_USER, reason="action_id_mismatch")
    if check_at >= contract.expires_at:
        return OutcomeValidation(state=ActionState.NEEDS_USER, reason="authorization_expired")
    if outcome.fee_inr is not None and outcome.fee_inr > 0:
        return OutcomeValidation(state=ActionState.NEEDS_USER, reason="unexpected_fee")
    if outcome.outcome is not CallOutcomeKind.CONFIRMED:
        return OutcomeValidation(state=ActionState.NEEDS_USER, reason=_outcome_reason(outcome.outcome))
    if outcome.requested_transfer:
        return OutcomeValidation(state=ActionState.NEEDS_USER, reason="transfer_requested")

    values: dict[str, object | None] = {
        "venue": outcome.venue,
        "date": outcome.date,
        "party_size": outcome.party_size,
        "confirmed_time": outcome.confirmed_time,
    }
    missing = sorted(
        evidence
        for evidence in contract.required_evidence
        if evidence not in values or values[evidence] is None
    )
    if missing:
        return OutcomeValidation(
            state=ActionState.NEEDS_USER,
            reason="missing_required_evidence",
            missing_evidence=missing,
        )

    if outcome.confirmed_time is not None:
        allowed_times = _authorized_times(set(contract.authorized_options))
        if not allowed_times:
            return OutcomeValidation(state=ActionState.NEEDS_USER, reason="unbounded_confirmed_time")
        normalized_time = outcome.confirmed_time.replace(microsecond=0)
        if normalized_time not in allowed_times:
            return OutcomeValidation(state=ActionState.NEEDS_USER, reason="unlisted_confirmed_time")

    return OutcomeValidation(state=ActionState.SUCCEEDED, reason="confirmed_within_bounds")


class Commitment(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    source_event_key: NonEmptyString
    summary: NonEmptyString
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)
    correlation_id: NonEmptyString | None = None
    # Phase 3 extension. Every field below is optional so a Phase 1/2 commitment
    # still validates. summary/starts_at/ends_at already carry Phase 3's
    # "title"/"planned_start"/"planned_end" concepts; this adds only what is
    # genuinely new: the flexibility window and repair-planning attributes.
    type: NonEmptyString | None = None
    earliest_start: AwareDatetime | None = None
    latest_start: AwareDatetime | None = None
    location_place_id: NonEmptyString | None = None
    criticality: Literal["LOW", "NORMAL", "HIGH", "CRITICAL"] | None = None
    flexibility: Literal["FLEXIBLE", "NEGOTIABLE", "FIXED", "NEVER_MOVE"] | None = None
    required_buffer_minutes: int = Field(default=0, ge=0)
    participants: list[NonEmptyString] = Field(default_factory=list)
    protected: bool = False
    pickup_selection: Literal["no_pickup", "selected"] | None = None
    pickup_command_fingerprint: NonEmptyString | None = None


class Edge(ContractModel):
    id: NonEmptyString
    from_ref: NonEmptyString
    to_ref: NonEmptyString
    relation: NonEmptyString
    # Phase 3 extension. `relation` stays a free label for compatibility;
    # `kind` is the closed set the planner's feasibility engine switches on.
    kind: (
        Literal[
            "must_finish_before",
            "requires_travel",
            "depends_on",
            "social_dependency",
            "requires_location",
            "same_resource",
        ]
        | None
    ) = None
    min_gap_minutes: int = Field(default=0, ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)


class Provenance(ContractModel):
    """How a persisted record came to exist, and how sure the source was."""

    source: Literal["gmail", "calendar", "vapi"]
    confidence: float = Field(ge=0, le=1)


class GmailEvidenceRef(ContractModel):
    """The Gmail message and history point a disruption was read from."""

    message_id: NonEmptyString
    history_id: int = Field(ge=0)


class Disruption(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    source_event_key: NonEmptyString
    kind: NonEmptyString
    occurred_at: AwareDatetime
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)
    correlation_id: NonEmptyString | None = None
    # Phase 2 extension. Every field below is optional so a Phase 1 disruption
    # still validates, and this stays the one canonical disruption record.
    commitment_id: NonEmptyString | None = None
    gmail_source: GmailEvidenceRef | None = None
    provider: str | None = None
    encrypted_booking_reference: NonEmptyString | None = None
    previous_time: AwareDatetime | None = None
    new_time: AwareDatetime | None = None
    location_text: str | None = None
    evidence_excerpt: str | None = Field(default=None, max_length=500)
    model_version: NonEmptyString | None = None
    match_score: int | None = Field(default=None, ge=0)
    match_reasons: list[NonEmptyString] = Field(default_factory=list)
    provenance: Provenance | None = None


class ProviderEvent(ContractModel):
    id: NonEmptyString
    action_id: NonEmptyString
    provider: Literal["vapi", "twilio", "calendar", "uber"]
    provider_event_key: NonEmptyString
    event_type: NonEmptyString | None = None
    provider_ref: NonEmptyString | None = None
    payload_hash: NonEmptyString | None = None
    occurred_at: AwareDatetime
    correlation_id: NonEmptyString
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)


class SourceEventEnvelope(ContractModel):
    source: Literal["gmail", "calendar", "vapi"]
    source_event_key: NonEmptyString
    occurred_at: AwareDatetime
    payload: dict[str, JsonValue]
    correlation_id: NonEmptyString


class DispatchClaim(ContractModel):
    claimed: bool
    action: ActionRecord | None = None
    reconciliation_required: bool = False


class AuditLogEntry(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    outcome: NonEmptyString
    correlation_id: NonEmptyString
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)
    source_event_key: NonEmptyString | None = None
    # Counts, hashes, and reasons only. Never message content.
    payload: dict[str, NonEmptyString] = Field(default_factory=dict)


class Problem(ContractModel):
    code: NonEmptyString
    message: NonEmptyString
    correlation_id: NonEmptyString
