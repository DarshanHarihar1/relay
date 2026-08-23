from __future__ import annotations

from pydantic import Field, model_validator

from app.contracts import AwareDatetime, ContractModel, NonEmptyString


class TimeInterval(ContractModel):
    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode="after")
    def end_must_follow_start(self) -> "TimeInterval":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class PlaceRef(ContractModel):
    """A provider-neutral place reference used only for a context lookup."""

    place_id: NonEmptyString | None = None
    address: NonEmptyString | None = None

    @model_validator(mode="after")
    def requires_lookup_value(self) -> "PlaceRef":
        if self.place_id is None and self.address is None:
            raise ValueError("A place reference requires a place_id or address")
        return self


class RouteEstimate(ContractModel):
    origin: PlaceRef
    destination: PlaceRef
    departure_time: AwareDatetime
    duration_seconds: int = Field(ge=0)
    distance_meters: int = Field(ge=0)


class PlaceDetails(ContractModel):
    place_id: NonEmptyString
    display_name: NonEmptyString
    address: NonEmptyString
    phone_number: str | None = None


class CalendarWindow(ContractModel):
    window: TimeInterval
    busy: list[TimeInterval] = Field(default_factory=list)


class PickerPhone(ContractModel):
    label: str | None = Field(default=None, max_length=100)
    number: NonEmptyString


class PickerContact(ContractModel):
    """A transient, purpose-limited result from the selected-contact picker."""

    display_name: str = Field(min_length=1, max_length=200)
    phones: list[PickerPhone] = Field(min_length=1)
    avatar_url: str | None = None
