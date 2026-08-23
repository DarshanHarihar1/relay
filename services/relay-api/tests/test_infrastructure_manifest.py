from pathlib import Path


def test_secret_manifest_contains_names_but_no_values():
    values = Path("../../infra/gcp/secret-manifest.txt").read_text().splitlines()
    assert "GOOGLE_OAUTH_CLIENT_SECRET" in values
    assert "VAPI_PRIVATE_KEY" in values
    assert all("=" not in value for value in values if value)


def test_cloud_run_specs_do_not_define_plaintext_secret_envs():
    secret_names = set(Path("../../infra/gcp/secret-manifest.txt").read_text().split())
    for service in ("relay-api", "relay-worker"):
        spec = Path(f"../../infra/gcp/cloudrun/{service}.service.yaml").read_text()
        assert "secretKeyRef" in spec
        env_name = None
        for line in spec.splitlines():
            stripped = line.strip()
            if stripped.startswith("- name: "):
                env_name = stripped.removeprefix("- name: ")
            elif stripped.startswith("value: "):
                assert env_name not in secret_names, f"{env_name} must use secretKeyRef"


def test_gmail_push_is_authenticated_and_bounded():
    pubsub = Path("../../infra/gcp/pubsub.sh").read_text()
    assert "--push-auth-service-account" in pubsub
    assert "--max-delivery-attempts=5" in pubsub
    assert "--dead-letter-topic" in pubsub
    assert "/v1/events/gmail" in pubsub

    worker_spec = Path("../../infra/gcp/cloudrun/relay-worker.service.yaml").read_text()
    assert "run.googleapis.com/ingress: internal" in worker_spec
    assert 'run.googleapis.com/invoker-iam-disabled: "false"' in worker_spec


def test_daily_retention_cleanup_is_scheduled_with_an_oidc_identity():
    pubsub = Path("../../infra/gcp/pubsub.sh").read_text()
    assert "/internal/maintenance/daily" in pubsub
    assert "--oidc-service-account-email" in pubsub
    assert "--oidc-token-audience" in pubsub

    worker_spec = Path("../../infra/gcp/cloudrun/relay-worker.service.yaml").read_text()
    assert "relay.dev/maintenance-route: /internal/maintenance/daily" in worker_spec
