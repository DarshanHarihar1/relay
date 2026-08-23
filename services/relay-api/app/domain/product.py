from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.contracts import ContractModel, NonEmptyString


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime values must include a timezone offset")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("Product timestamps must be expressed in UTC")
    return value


class ProductModel(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimelineStatus(StrEnum):
    CHANGED = "changed"
    AT_RISK = "at_risk"
    REPAIRED = "repaired"
    UNRESOLVED = "unresolved"
    PROTECTED = "protected"


class OutcomeStatus(StrEnum):
    VERIFIED = "verified"
    IN_PROGRESS = "in_progress"
    RETRYING = "retrying"
    NEEDS_USER = "needs_user"
    FAILED = "failed"
    HANDOFF = "handoff"


class PlanTimelineItem(ProductModel):
    commitment_id: NonEmptyString
    title: str = Field(min_length=1, max_length=140)
    starts_at: datetime
    ends_at: datetime
    status: TimelineStatus
    explanation: str = Field(min_length=1, max_length=360)
    is_pickup_prompt: bool = False
    pickup_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "PlanTimelineItem":
        _utc_datetime(self.starts_at)
        _utc_datetime(self.ends_at)
        if self.ends_at <= self.starts_at:
            raise ValueError("Timeline item must end after it starts")
        return self


class ApprovalActionSummary(ProductModel):
    action_id: NonEmptyString
    kind: Literal["voice_call", "calendar_hold", "uber_deep_link"]
    goal: str = Field(min_length=1, max_length=160)
    authorized_options: tuple[str, ...]
    max_fee_inr: int = Field(ge=0)
    expires_at: datetime | None
    disclosure: str | None = Field(default=None, max_length=240)
    must_not: tuple[str, ...]

    @model_validator(mode="after")
    def validate_expiry(self) -> "ApprovalActionSummary":
        if self.expires_at is not None:
            _utc_datetime(self.expires_at)
        return self


class ApprovalBatchView(ProductModel):
    approval_id: NonEmptyString
    version: int = Field(ge=1)
    state: Literal["awaiting_approval", "approved", "declined", "expired", "blocked"]
    expires_at: datetime
    reason: str = Field(min_length=1, max_length=360)
    actions: tuple[ApprovalActionSummary, ...]

    @model_validator(mode="after")
    def validate_expiry(self) -> "ApprovalBatchView":
        _utc_datetime(self.expires_at)
        return self


class ActionOutcomeView(ProductModel):
    action_id: NonEmptyString
    kind: Literal["voice_call", "calendar_hold", "uber_deep_link"]
    status: OutcomeStatus
    summary: str = Field(min_length=1, max_length=360)
    occurred_at: datetime
    evidence_label: str | None = Field(default=None, max_length=180)
    retry_at: datetime | None = None
    handoff_url: str | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> "ActionOutcomeView":
        _utc_datetime(self.occurred_at)
        if self.retry_at is not None:
            _utc_datetime(self.retry_at)
        return self


class AuditEventView(ProductModel):
    occurred_at: datetime
    event_code: NonEmptyString
    summary: str = Field(min_length=1, max_length=360)

    @model_validator(mode="after")
    def validate_timestamp(self) -> "AuditEventView":
        _utc_datetime(self.occurred_at)
        return self


class ActionAuditView(ProductModel):
    outcome: ActionOutcomeView
    events: tuple[AuditEventView, ...]


class PickupContactCommand(ProductModel):
    selection: Literal["no_pickup", "google_picker", "manual"]
    picker_session_id: str | None = None
    picker_contact_index: int | None = Field(default=None, ge=0, le=19)
    manual_display_name: str | None = Field(default=None, min_length=1, max_length=200)
    manual_phone_number: str | None = Field(default=None, min_length=7, max_length=32)
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_exclusive_selection(self) -> "PickupContactCommand":
        picker_fields = (self.picker_session_id, self.picker_contact_index)
        manual_fields = (self.manual_display_name, self.manual_phone_number)
        if self.selection == "no_pickup" and any(value is not None for value in (*picker_fields, *manual_fields)):
            raise ValueError("no_pickup cannot include contact fields")
        if self.selection == "google_picker":
            if self.picker_session_id is None or self.picker_contact_index is None:
                raise ValueError("google_picker requires a current picker result")
            if any(value is not None for value in manual_fields):
                raise ValueError("google_picker cannot include manual contact fields")
        if self.selection == "manual":
            if self.manual_display_name is None or self.manual_phone_number is None:
                raise ValueError("manual selection requires a name and phone number")
            if any(value is not None for value in picker_fields):
                raise ValueError("manual selection cannot include picker fields")
        return self


class PickupContactResponse(ProductModel):
    commitment_id: NonEmptyString
    version: int = Field(ge=1)
    selection: Literal["no_pickup", "selected"]
    display_name: str | None = None


class DashboardView(ProductModel):
    repair_plan_id: NonEmptyString
    repair_plan_version: int = Field(ge=1)
    generated_at: datetime
    timeline: tuple[PlanTimelineItem, ...]
    approval: ApprovalBatchView | None
    outcomes: tuple[ActionOutcomeView, ...]
    last_event_id: str | None

    @model_validator(mode="after")
    def validate_generated_at(self) -> "DashboardView":
        _utc_datetime(self.generated_at)
        return self


class RegisterDeviceRequest(ProductModel):
    token: str = Field(min_length=32, max_length=4096)
    platform: Literal["web"] = "web"


class PickerPhoneView(ProductModel):
    label: str | None = Field(default=None, max_length=100)
    last4: str = Field(pattern=r"^\d{4}$")


class PickerContactView(ProductModel):
    display_name: str = Field(min_length=1, max_length=200)
    phones: tuple[PickerPhoneView, ...]


class PickerSessionView(ProductModel):
    session_id: NonEmptyString
    contacts: tuple[PickerContactView, ...]


__all__ = [
    "ActionAuditView",
    "ActionOutcomeView",
    "ApprovalActionSummary",
    "ApprovalBatchView",
    "AuditEventView",
    "DashboardView",
    "OutcomeStatus",
    "PickupContactCommand",
    "PickupContactResponse",
    "PickerContactView",
    "PickerPhoneView",
    "PickerSessionView",
    "PlanTimelineItem",
    "RegisterDeviceRequest",
    "TimelineStatus",
]
