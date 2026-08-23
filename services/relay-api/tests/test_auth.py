from fastapi.testclient import TestClient

from app.main import app


def test_require_current_user_rejects_missing_bearer_token():
    response = TestClient(app).get("/v1/me")

    assert response.status_code == 401


def test_require_current_user_rejects_a_user_id_header():
    response = TestClient(app).get("/v1/me", headers={"user_id": "untrusted-user"})

    assert response.status_code == 401
