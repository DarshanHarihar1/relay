from pathlib import Path


def test_ci_runs_contract_drift_check_before_application_tests():
    workflow = Path("../../.github/workflows/ci.yml").read_text()
    assert "pnpm check:contracts" in workflow
    assert workflow.index("pnpm check:contracts") < workflow.index("pnpm test:web")


def test_ci_does_not_inject_production_secret_values():
    workflow = Path("../../.github/workflows/ci.yml").read_text()
    assert "VAPI_PRIVATE_KEY" not in workflow
    assert "TWILIO_AUTH_TOKEN" not in workflow


def test_ci_has_deterministic_product_e2e_and_explicit_browser_gate():
    workflow = Path("../../.github/workflows/ci.yml").read_text()
    assert "tests/e2e -q -m e2e" in workflow
    assert "RELAY_E2E_ENABLED" in workflow
