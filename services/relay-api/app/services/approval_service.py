from __future__ import annotations

from app.contracts import ApprovalDecisionRequest, ApprovalDecisionResponse
from app.repositories.actions import ActionRepository


class ApprovalService:
    """Application boundary for deciding one persisted approval batch."""

    def __init__(self, repository: ActionRepository) -> None:
        self._repository = repository

    async def decide(
        self,
        approval_id: str,
        *,
        user_id: str,
        request: ApprovalDecisionRequest,
        correlation_id: str,
    ) -> ApprovalDecisionResponse:
        if request.approval_id != approval_id:
            raise LookupError
        return await self._repository.decide_approval(user_id, request, correlation_id)
