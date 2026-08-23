from __future__ import annotations

import pytest

from app.contracts import ApprovalDecisionRequest, ApprovalDecisionResponse
from app.services.approval_service import ApprovalService


class RecordingApprovalRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, ApprovalDecisionRequest, str]] = []

    async def decide_approval(
        self,
        user_id: str,
        request: ApprovalDecisionRequest,
        correlation_id: str,
    ) -> ApprovalDecisionResponse:
        self.calls.append((user_id, request.approval_id, request, correlation_id))
        return ApprovalDecisionResponse(
            approval_id=request.approval_id,
            state="approved",
            action_ids=["action-1"],
        )


@pytest.mark.asyncio
async def test_approval_service_uses_the_persisted_batch_and_correlation_id() -> None:
    repository = RecordingApprovalRepository()
    service = ApprovalService(repository)

    result = await service.decide(
        "approval-1",
        user_id="user-1",
        request=ApprovalDecisionRequest(
            approval_id="approval-1",
            decision="approve",
            expected_version=1,
        ),
        correlation_id="corr-1",
    )

    assert result.action_ids == ["action-1"]
    assert repository.calls[0][0] == "user-1"
    assert repository.calls[0][3] == "corr-1"


@pytest.mark.asyncio
async def test_approval_service_rejects_a_path_body_id_mismatch() -> None:
    repository = RecordingApprovalRepository()

    with pytest.raises(LookupError):
        await ApprovalService(repository).decide(
            "approval-from-path",
            user_id="user-1",
            request=ApprovalDecisionRequest(
                approval_id="approval-from-body",
                decision="approve",
                expected_version=1,
            ),
            correlation_id="corr-1",
        )
