from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient, async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter

from app.contracts import ActionRecord, Approval, Commitment, Edge
from app.domain.impact import ImpactAssessment, RepairPlan, make_assessment_id, make_repair_plan_id
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document


class RelayRepository(Protocol):
    async def get_commitment(self, *, user_id: str, commitment_id: str) -> Commitment | None: ...

    async def get_commitments(self, *, user_id: str, commitment_ids: Sequence[str]) -> list[Commitment]: ...

    async def list_outgoing_edges(self, *, user_id: str, from_id: str) -> list[Edge]: ...

    async def get_repair_plan_by_fingerprint(
        self, *, user_id: str, disruption_id: str, input_fingerprint: str
    ) -> RepairPlan | None: ...

    async def save_planning_result(
        self,
        *,
        user_id: str,
        assessment: ImpactAssessment,
        plan: RepairPlan,
        action_records: Sequence[ActionRecord],
        approval: Approval | None,
    ) -> RepairPlan: ...


class FirestoreRelayRepository:
    """Read-only graph access, plus write-once phase-03 planning persistence.

    Never mutates a commitment, edge, or dispatched action; a repair plan is
    written exactly once per deterministic (disruption, input) fingerprint.
    """

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    async def get_commitment(self, *, user_id: str, commitment_id: str) -> Commitment | None:
        snapshot = await self._client.document(user_document(user_id, "commitments", commitment_id)).get()
        if not snapshot.exists:
            return None
        return Commitment.model_validate(as_aware_datetimes(snapshot.to_dict()))

    async def get_commitments(self, *, user_id: str, commitment_ids: Sequence[str]) -> list[Commitment]:
        commitments = []
        for commitment_id in commitment_ids:
            commitment = await self.get_commitment(user_id=user_id, commitment_id=commitment_id)
            if commitment is not None:
                commitments.append(commitment)
        return commitments

    async def list_outgoing_edges(self, *, user_id: str, from_id: str) -> list[Edge]:
        query = self._client.collection(f"users/{user_id}/edges").where(
            filter=FieldFilter("from_ref", "==", from_id)
        )
        return [Edge.model_validate(as_aware_datetimes(snapshot.to_dict())) async for snapshot in query.stream()]

    async def get_repair_plan(self, *, user_id: str, plan_id: str) -> RepairPlan | None:
        snapshot = await self._client.document(user_document(user_id, "repair_plans", plan_id)).get()
        if not snapshot.exists:
            return None
        return RepairPlan.model_validate(snapshot.to_dict())

    async def get_repair_plan_by_fingerprint(
        self, *, user_id: str, disruption_id: str, input_fingerprint: str
    ) -> RepairPlan | None:
        # Plan version 1's ID is fully deterministic from (disruption_id,
        # input_fingerprint); a repeat of the same inputs is a plain read.
        assessment_id = make_assessment_id(disruption_id, input_fingerprint)
        plan_id = make_repair_plan_id(assessment_id, 1)
        return await self.get_repair_plan(user_id=user_id, plan_id=plan_id)

    async def save_planning_result(
        self,
        *,
        user_id: str,
        assessment: ImpactAssessment,
        plan: RepairPlan,
        action_records: Sequence[ActionRecord],
        approval: Approval | None,
    ) -> RepairPlan:
        plan_ref = self._client.document(user_document(user_id, "repair_plans", plan.id))
        assessment_ref = self._client.document(user_document(user_id, "impact_assessments", assessment.id))
        action_refs = [
            self._client.document(user_document(user_id, "actions", record.id)) for record in action_records
        ]
        approval_ref = (
            self._client.document(user_document(user_id, "approvals", approval.id)) if approval is not None else None
        )

        @async_transactional
        async def write(transaction) -> RepairPlan:
            existing = await plan_ref.get(transaction=transaction)
            if existing.exists:
                return RepairPlan.model_validate(existing.to_dict())
            transaction.create(assessment_ref, firestore_data(assessment))
            transaction.create(plan_ref, firestore_data(plan))
            for ref, record in zip(action_refs, action_records, strict=True):
                transaction.create(ref, firestore_data(record))
            if approval_ref is not None:
                transaction.create(approval_ref, firestore_data(approval))
            return plan

        return await write(self._client.transaction())
