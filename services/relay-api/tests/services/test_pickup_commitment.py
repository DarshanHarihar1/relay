from datetime import datetime, timezone

import pytest

from app.domain.ingestion import SelectedContact
from app.domain.product import PickupContactCommand
from app.domain.product import PickupContactResponse
from app.services.contact_selection import ContactChoice
from app.services.pickup_commitment import PickupCommitmentService


class FakeSelection:
    async def select_picker_contact(self, *, user_id: str, session_id: str, contact_index: int) -> ContactChoice:
        if user_id != "u1" or session_id != "session-1" or contact_index != 0:
            raise ValueError("current picker")
        return ContactChoice(display_name="Rohan", phone_number="+919876543210", source="google_picker")

    async def build_selected_contact(self, *, user_id: str, choice: ContactChoice) -> SelectedContact:
        return SelectedContact(
            display_name=choice.display_name,
            encrypted_phone_number="enc:v1:opaque",
            phone_last4="3210",
            source=choice.source,
            selected_at=datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc),
        )


class FakeRepository:
    def __init__(self) -> None:
        self.calls = []

    async def update_pickup_if_version(self, **kwargs) -> PickupContactResponse:
        self.calls.append(kwargs)
        return PickupContactResponse(
            commitment_id=kwargs["commitment_id"],
            version=kwargs["expected_version"] + 1,
            selection=kwargs["selection"],
            display_name=(kwargs["selected_contact"].display_name if kwargs["selected_contact"] else None),
        )


@pytest.mark.asyncio
async def test_no_pickup_requires_explicit_command_and_is_idempotency_ready() -> None:
    repository = FakeRepository()
    service = PickupCommitmentService(repository, FakeSelection())
    command = PickupContactCommand(selection="no_pickup", expected_version=3)

    first = await service.submit_pickup_contact(
        user_id="u1", commitment_id="pickup_1", command=command, correlation_id="corr-1"
    )
    second = await service.submit_pickup_contact(
        user_id="u1", commitment_id="pickup_1", command=command, correlation_id="corr-1"
    )

    assert first == second
    assert len(repository.calls) == 2
    assert repository.calls[0]["selected_contact"] is None
    assert repository.calls[0]["command_fingerprint"] == repository.calls[1]["command_fingerprint"]


@pytest.mark.asyncio
async def test_google_picker_selection_never_passes_plain_phone_to_repository() -> None:
    repository = FakeRepository()
    service = PickupCommitmentService(repository, FakeSelection())
    command = PickupContactCommand(
        selection="google_picker",
        picker_session_id="session-1",
        picker_contact_index=0,
        expected_version=2,
    )

    await service.submit_pickup_contact(
        user_id="u1", commitment_id="pickup_1", command=command, correlation_id="corr-1"
    )

    assert "+919876543210" not in str(repository.calls[0])
    assert repository.calls[0]["selected_contact"].encrypted_phone_number.startswith("enc:v1:")
