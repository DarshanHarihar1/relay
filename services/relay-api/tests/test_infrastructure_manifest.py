from pathlib import Path


def test_secret_manifest_contains_names_but_no_values():
    values = Path("../../infra/gcp/secret-manifest.txt").read_text().splitlines()
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in values
    assert "VAPI_PRIVATE_KEY" in values
    assert all("=" not in value for value in values if value)


def test_cloud_run_specs_do_not_define_plaintext_secret_envs():
    api_spec = Path("../../infra/gcp/cloudrun/relay-api.service.yaml").read_text()
    assert "secretKeyRef" in api_spec
    assert "value: " not in api_spec
