from pathlib import Path


ROOT = Path(__file__).parents[4]


def test_api_manifest_is_load_balancer_only_and_keeps_webhook_secret_referenced():
    manifest = (ROOT / "infra/gcp/cloudrun/relay-api.service.yaml").read_text()
    assert "run.googleapis.com/ingress: internal-and-cloud-load-balancing" in manifest
    assert "VAPI_WEBHOOK_SECRET" in manifest
    assert "VAPI_WEBHOOK_SECRET=" not in manifest
    assert "secretKeyRef:" in manifest
    assert "startupProbe:" in manifest
    assert "containerConcurrency:" in manifest


def test_worker_manifest_is_private_and_revision_hardened():
    manifest = (ROOT / "infra/gcp/cloudrun/relay-worker.service.yaml").read_text()
    assert "run.googleapis.com/ingress: internal" in manifest
    assert 'run.googleapis.com/invoker-iam-disabled: "false"' in manifest
    assert "relay-worker-sa@__PROJECT_ID__.iam.gserviceaccount.com" in manifest
    assert "startupProbe:" in manifest
    assert "resources:" in manifest


def test_smoke_script_uses_only_explicit_origin_and_stdin_token():
    script = (ROOT / "scripts/smoke-demo.sh").read_text()
    assert 'origin="${RELAY_API_ORIGIN:?' in script
    assert 'token="$(cat)"' in script
    assert "approval" not in script.lower()
    assert "provider_ref" in script
