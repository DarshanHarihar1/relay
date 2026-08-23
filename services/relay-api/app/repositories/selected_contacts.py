from __future__ import annotations

from google.cloud.firestore_v1 import AsyncClient

from app.domain.ingestion import SelectedContact
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document


class FirestoreSelectedContactStore:
    """One explicitly chosen pickup contact per commitment.

    The phone number arrives already encrypted. A contact exists only because
    the user selected it, and it is removed with its commitment reference.
    """

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def save_selected_contact(
        self, *, user_id: str, commitment_id: str, selected: SelectedContact
    ) -> None:
        await self._document(user_id, commitment_id).set(firestore_data(selected))

    async def get_selected_contact(
        self, *, user_id: str, commitment_id: str
    ) -> SelectedContact | None:
        snapshot = await self._document(user_id, commitment_id).get()
        if not snapshot.exists:
            return None
        return SelectedContact.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def remove_selected_contact(self, *, user_id: str, commitment_id: str) -> None:
        await self._document(user_id, commitment_id).delete()

    def _document(self, user_id: str, commitment_id: str):
        return self._client.document(
            user_document(user_id, "selected_contacts", commitment_id)
        )


__all__ = ["FirestoreSelectedContactStore"]
