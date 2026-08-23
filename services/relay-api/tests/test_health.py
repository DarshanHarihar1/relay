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


def test_info_level_operational_logs_actually_reach_a_handler():
    """A prior regression here: the root logger had no configured handler, so
    every INFO-level operational log (extraction outcomes, ingestion summaries,
    daily maintenance counts) was silently dropped in production. Only
    WARNING+ escaped, via Python's built-in last-resort stderr handler."""
    import logging

    import app.main  # noqa: F401 - import triggers the module-level logging setup

    ingestion_logger = logging.getLogger("relay.ingestion")
    assert ingestion_logger.getEffectiveLevel() <= logging.INFO
    assert logging.getLogger().handlers, "the root logger must have a handler"
