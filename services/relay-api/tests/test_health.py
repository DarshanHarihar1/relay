from fastapi.testclient import TestClient

from app.main import app


def test_health_check_is_public():
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_served_on_a_path_cloud_run_does_not_intercept():
    """Google Front End answers /healthz itself, so /health must also exist."""
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
