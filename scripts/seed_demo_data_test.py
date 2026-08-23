from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).with_name("seed-demo-data.py")
    spec = importlib.util.spec_from_file_location("seed_demo_data", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_keeps_protected_commitment_out_of_candidates():
    module = _module()
    seed = module.DemoSeeder(module.InMemoryDemoRepository())
    now = module.datetime.fromisoformat("2026-08-22T16:00:00+00:00")

    first = await seed.seed_demo_data(user_id="demo", now=now, mode="rehearsal")
    second = await seed.seed_demo_data(user_id="demo", now=now, mode="rehearsal")

    assert first == second
    assert await seed.repo.count_commitments(user_id="demo") == 4
    assert "protected_unrelated" not in (await seed.repo.get_plan(first.repair_plan_id)).model_dump_json()


def test_runbook_declares_consent_and_honest_unresolved_state():
    text = (Path(__file__).parent.parent / "docs/demo-runbook.md").read_text()
    assert "pre-consented" in text
    assert "visibly labelled mock" in text
    assert "Never say the ride is booked" in text
