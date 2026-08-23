from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from app.domain.ingestion import SelectedContact
from app.domain.product import PickupContactCommand, PickupContactResponse
from app.repositories.product import ProductRepository
from app.services.contact_selection import ContactChoice


class PickupSelectionPort(Protocol):
    async def select_picker_contact(
        self, *, user_id: str, session_id: str, contact_index: int
    ) -> ContactChoice: ...

    async def build_selected_contact(
        self, *, user_id: str, choice: ContactChoice
    ) -> SelectedContact: ...


class PickupCommitmentService:
    def __init__(self, repository: ProductRepository, selection: PickupSelectionPort) -> None:
        self._repository = repository
        self._selection = selection

    async def submit_pickup_contact(
        self, *, user_id: str, commitment_id: str, command: PickupContactCommand, correlation_id: str
    ) -> PickupContactResponse:
        selected: SelectedContact | None = None
        if command.selection == "google_picker":
            # Pydantic has already proved both fields are present for this branch.
            choice = await self._selection.select_picker_contact(
                user_id=user_id,
                session_id=command.picker_session_id or "",
                contact_index=command.picker_contact_index or 0,
            )
            selected = await self._selection.build_selected_contact(user_id=user_id, choice=choice)
        elif command.selection == "manual":
            choice = ContactChoice(
                display_name=command.manual_display_name or "",
                phone_number=command.manual_phone_number or "",
                source="manual",
            )
            selected = await self._selection.build_selected_contact(user_id=user_id, choice=choice)

        fingerprint = _command_fingerprint(command)
        return await self._repository.update_pickup_if_version(
            user_id=user_id,
            commitment_id=commitment_id,
            expected_version=command.expected_version,
            selection="no_pickup" if command.selection == "no_pickup" else "selected",
            selected_contact=selected,
            command_fingerprint=fingerprint,
            correlation_id=correlation_id,
        )


def _command_fingerprint(command: PickupContactCommand) -> str:
    # The raw phone is hashed only to make an idempotency key. It is never used
    # as a document ID, log field, audit payload, or response value.
    canonical = "|".join(
        [
            command.selection,
            command.picker_session_id or "",
            str(command.picker_contact_index) if command.picker_contact_index is not None else "",
            command.manual_display_name or "",
            command.manual_phone_number or "",
            str(command.expected_version),
        ]
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["PickupCommitmentService", "PickupSelectionPort"]
