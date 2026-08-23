from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.context import CalendarWindow, PickerContact, PlaceDetails, PlaceRef, RouteEstimate, TimeInterval
from app.domain.ingestion import GmailMessage, GoogleConnection, HistoryPage, WatchRegistration


class CalendarPort(Protocol):
    async def get_busy(
        self,
        *,
        connection: GoogleConnection,
        window: TimeInterval,
        calendar_id: str = "primary",
    ) -> CalendarWindow: ...


class RoutesPort(Protocol):
    async def compute_route(
        self,
        *,
        origin: PlaceRef,
        destination: PlaceRef,
        departure_time: datetime,
    ) -> RouteEstimate: ...


class PlacesPort(Protocol):
    async def get_details(self, *, place_id: str) -> PlaceDetails: ...


class GmailPort(Protocol):
    async def ensure_watch(self, *, connection: GoogleConnection) -> WatchRegistration: ...

    async def list_history(
        self,
        *,
        connection: GoogleConnection,
        start_history_id: int,
    ) -> HistoryPage: ...

    async def get_message(self, *, connection: GoogleConnection, message_id: str) -> GmailMessage: ...


class PeoplePort(Protocol):
    async def search_contacts(
        self,
        *,
        connection: GoogleConnection,
        query: str,
        page_size: int = 20,
    ) -> list[PickerContact]: ...
