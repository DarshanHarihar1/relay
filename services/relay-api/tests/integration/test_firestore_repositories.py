from datetime import datetime, timezone

import pytest

from app.contracts import ActionRecord
from app.repositories.firestore import user_document


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def minimal_action(*, user_id: str, id: str) -> ActionRecord:
    return ActionRecord(
        id=id,
        user_id=user_id,
        repair_plan_id="plan-1",
        repair_plan_version=1,
        type="calendar_hold",
        target_ref="calendar:primary",
        idempotency_key=f"plan-1:{id}",
        authorization_snapshot={
            "type": "calendar_hold",
            "calendar_id": "primary",
            "start_at": NOW,
            "end_at": datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc),
            "visibility": "private",
        },
        state="planned",
        correlation_id="correlation-1",
    )


@pytest.mark.emulator
async def test_action_repository_never_reads_another_users_document(actions):
    await actions.create(minimal_action(user_id="user-a", id="act-1"))

    assert await actions.get("user-b", "act-1") is None


@pytest.mark.emulator
async def test_action_documents_round_trip_with_timezone_aware_timestamps(actions):
    created = await actions.create(minimal_action(user_id="user-a", id="act-1"))

    restored = await actions.get("user-a", "act-1")

    assert restored == created
    assert restored is not None
    assert restored.created_at.tzinfo is not None
    assert restored.updated_at.tzinfo is not None


def test_user_document_rejects_cross_namespace_paths():
    assert user_document("user-a", "actions", "act-1") == "users/user-a/actions/act-1"

    with pytest.raises(ValueError):
        user_document("user-a", "unknown", "act-1")

    with pytest.raises(ValueError):
        user_document("", "actions", "act-1")
