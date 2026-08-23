from app.config import Settings


def test_settings_rejects_missing_project_id(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert Settings.from_env().google_cloud_project is None
