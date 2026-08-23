from __future__ import annotations

from datetime import datetime, timezone
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
    max_fee_inr: int = Field(ge=1, le=100000)
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
        return self


class Approval(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    action_ids: list[NonEmptyString] = Field(min_length=1)
    state: Literal["pending", "approved", "declined"]
    version: int = Field(ge=1)
    correlation_id: NonEmptyString
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApprovalDecisionRequest(ContractModel):
    approval_id: NonEmptyString
    decision: Literal["approve", "decline"]
    expected_version: int = Field(ge=1)


class ApprovalDecisionResponse(ContractModel):
    approval_id: NonEmptyString
    state: Literal["approved", "declined"]
    action_ids: list[NonEmptyString]


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


class Edge(ContractModel):
    id: NonEmptyString
    from_ref: NonEmptyString
    to_ref: NonEmptyString
    relation: NonEmptyString


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


class ProviderEvent(ContractModel):
    id: NonEmptyString
    action_id: NonEmptyString
    provider: Literal["vapi", "calendar", "uber"]
    provider_event_key: NonEmptyString
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


class AuditLogEntry(ContractModel):
    id: NonEmptyString
    user_id: NonEmptyString
    outcome: NonEmptyString
    correlation_id: NonEmptyString
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1, ge=1)
    source_event_key: NonEmptyString | None = None


class Problem(ContractModel):
    code: NonEmptyString
    message: NonEmptyString
    correlation_id: NonEmptyString
