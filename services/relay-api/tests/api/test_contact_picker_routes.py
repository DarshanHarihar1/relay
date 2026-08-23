from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.context import PickerContact, PickerPhone
from app.main import app
from app.routes.google import get_contact_selection_service
from app.services.contact_selection import ContactsPermissionRequired


def _auth_headers(monkeypatch) -> dict[str, str]:
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "relay-test")
    monkeypatch.setattr(
        "app.auth._verify_firebase_id_token",
        lambda token, project_id: {"aud": project_id, "uid": "user-1", "email": "user@example.com"},
    )
    return {"Authorization": "Bearer test-token"}


class FakeContactSelectionService:
    async def search_picker_contacts(self, *, user_id: str, query: str):
        assert user_id == "user-1"
        assert query == "Ro"
        return [
            PickerContact(
                display_name="Rohan",
                phones=[PickerPhone(label="mobile", number="+919876543210")],
                avatar_url=None,
            )
        ]

    async def select_pickup_contact(self, *, user_id: str, commitment_id: str, choice):
        assert user_id == "user-1"
        assert commitment_id == "pickup-1"

    async def remove_pickup_contact(self, *, user_id: str, commitment_id: str):
        assert user_id == "user-1"
        assert commitment_id == "pickup-1"


def test_contact_picker_routes_are_authenticated_and_scoped(monkeypatch) -> None:
    headers = _auth_headers(monkeypatch)
    app.dependency_overrides[get_contact_selection_service] = lambda: FakeContactSelectionService()
    try:
        client = TestClient(app)
        search = client.get("/v1/google/contacts?query=Ro", headers=headers)
        select = client.put(
            "/v1/commitments/pickup-1/pickup-contact",
            json={"display_name": "Rohan", "phone_number": "+919876543210", "source": "manual"},
            headers=headers,
        )
        delete = client.delete("/v1/commitments/pickup-1/pickup-contact", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert search.status_code == 200
    assert search.json()[0]["display_name"] == "Rohan"
    assert select.status_code == 204
    assert delete.status_code == 204


def test_contact_search_returns_a_permission_specific_problem(monkeypatch) -> None:
    class ContactsDisabled:
        async def search_picker_contacts(self, *, user_id: str, query: str):
            raise ContactsPermissionRequired

    app.dependency_overrides[get_contact_selection_service] = lambda: ContactsDisabled()
    try:
        response = TestClient(app).get("/v1/google/contacts?query=Ro", headers=_auth_headers(monkeypatch))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["code"] == "CONTACTS_PERMISSION_REQUIRED"
