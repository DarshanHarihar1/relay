from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from google.cloud.firestore_v1 import AsyncClient
from google.cloud.firestore_v1.async_transaction import async_transactional
from google.cloud.firestore_v1.base_query import FieldFilter

from app.contracts import ActionRecord, Approval, AuditLogEntry, Commitment
from app.domain.impact import ImpactAssessment, RepairPlan
from app.domain.ingestion import SelectedContact
from app.domain.product import PickupContactResponse
from app.repositories.firestore import as_aware_datetimes, firestore_data, user_document


class PickupVersionConflict(Exception):
    """The pickup commitment changed since the user last read it."""


@dataclass(frozen=True)
class DashboardSource:
    generated_at: datetime
    plan: RepairPlan
    assessment: ImpactAssessment | None
    commitments: tuple[Commitment, ...]
    approval: Approval | None
    actions: tuple[ActionRecord, ...]
    audits: tuple[AuditLogEntry, ...]


@dataclass(frozen=True)
class ActionAuditSource:
    action: ActionRecord
    audits: tuple[AuditLogEntry, ...]


class ProductRepository(Protocol):
    async def get_dashboard_source(self, *, user_id: str) -> DashboardSource | None: ...

    async def get_action_audit_source(
        self, *, user_id: str, action_id: str
    ) -> ActionAuditSource | None: ...

    async def update_pickup_if_version(
        self,
        *,
        user_id: str,
        commitment_id: str,
        expected_version: int,
        selection: Literal["no_pickup", "selected"],
        selected_contact: SelectedContact | None,
        command_fingerprint: str,
        correlation_id: str,
    ) -> PickupContactResponse: ...


class FirestoreProductRepository:
    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def _document(self, user_id: str, collection: str, document_id: str):
        return self._client.document(user_document(user_id, collection, document_id))

    async def get_dashboard_source(self, *, user_id: str) -> DashboardSource | None:
        plans = []
        async for snapshot in self._client.collection(f"users/{user_id}/repair_plans").stream():
            plans.append(
                (
                    _snapshot_created_at(snapshot),
                    RepairPlan.model_validate(as_aware_datetimes(snapshot.to_dict())),
                )
            )
        if not plans:
            return None
        generated_at, plan = max(plans, key=lambda item: (item[0], item[1].version, item[1].id))

        assessment_snapshot = await self._document(user_id, "impact_assessments", plan.assessment_id).get()
        assessment = (
            ImpactAssessment.model_validate(as_aware_datetimes(assessment_snapshot.to_dict()))
            if assessment_snapshot.exists
            else None
        )
        commitments = [
            Commitment.model_validate(as_aware_datetimes(snapshot.to_dict()))
            async for snapshot in self._client.collection(f"users/{user_id}/commitments").stream()
        ]
        approval = None
        if plan.approval_id is not None:
            approval_snapshot = await self._document(user_id, "approvals", plan.approval_id).get()
            if approval_snapshot.exists:
                approval = Approval.model_validate(as_aware_datetimes(approval_snapshot.to_dict()))
        actions = [
            ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
            async for snapshot in self._client.collection(f"users/{user_id}/actions")
            .where(filter=FieldFilter("repair_plan_id", "==", plan.id))
            .stream()
        ]
        audits = [
            AuditLogEntry.model_validate(as_aware_datetimes(snapshot.to_dict()))
            async for snapshot in self._client.collection(f"users/{user_id}/audit_log").stream()
        ]
        return DashboardSource(
            generated_at=generated_at,
            plan=plan,
            assessment=assessment,
            commitments=tuple(sorted(commitments, key=lambda item: (item.starts_at, item.id))),
            approval=approval,
            actions=tuple(sorted(actions, key=lambda item: item.id)),
            audits=tuple(audits),
        )

    async def get_action_audit_source(
        self, *, user_id: str, action_id: str
    ) -> ActionAuditSource | None:
        snapshot = await self._document(user_id, "actions", action_id).get()
        if not snapshot.exists:
            return None
        action = ActionRecord.model_validate(as_aware_datetimes(snapshot.to_dict()))
        audits = []
        async for audit_snapshot in self._client.collection(f"users/{user_id}/audit_log").stream():
            audit = AuditLogEntry.model_validate(as_aware_datetimes(audit_snapshot.to_dict()))
            if action_id in audit.id or audit.payload.get("action_id") == action_id:
                audits.append(audit)
        return ActionAuditSource(action=action, audits=tuple(audits))

    async def update_pickup_if_version(
        self,
        *,
        user_id: str,
        commitment_id: str,
        expected_version: int,
        selection: Literal["no_pickup", "selected"],
        selected_contact: SelectedContact | None,
        command_fingerprint: str,
        correlation_id: str,
    ) -> PickupContactResponse:
        commitment_document = self._document(user_id, "commitments", commitment_id)
        contact_document = self._document(user_id, "selected_contacts", commitment_id)
        audit_document = self._document(
            user_id, "audit_log", f"pickup-{commitment_id}-{command_fingerprint}"
        )

        @async_transactional
        async def update(transaction):
            snapshot = await commitment_document.get(transaction=transaction)
            if not snapshot.exists:
                raise LookupError
            commitment = Commitment.model_validate(as_aware_datetimes(snapshot.to_dict()))
            if commitment.version != expected_version:
                if (
                    commitment.pickup_command_fingerprint == command_fingerprint
                    and commitment.pickup_selection == selection
                ):
                    current_contact = await contact_document.get(transaction=transaction)
                    return _pickup_response(
                        commitment,
                        selection,
                        current_contact.to_dict() if current_contact.exists else None,
                    )
                raise PickupVersionConflict

            now = datetime.now(timezone.utc)
            updated = commitment.model_copy(
                update={
                    "pickup_selection": selection,
                    "pickup_command_fingerprint": command_fingerprint,
                    "updated_at": now,
                    "version": commitment.version + 1,
                    "correlation_id": correlation_id,
                }
            )
            transaction.update(commitment_document, firestore_data(updated))
            if selected_contact is None:
                transaction.delete(contact_document)
            else:
                transaction.set(contact_document, firestore_data(selected_contact))
            if not (await audit_document.get(transaction=transaction)).exists:
                audit = AuditLogEntry(
                    id=audit_document.id,
                    user_id=user_id,
                    outcome="PICKUP_DECLARED_NO" if selection == "no_pickup" else "PICKUP_CONTACT_SELECTED",
                    correlation_id=correlation_id,
                    created_at=now,
                    updated_at=now,
                    payload={"commitment_id": commitment_id, "selection": selection},
                )
                transaction.create(audit_document, firestore_data(audit))
            return _pickup_response(
                updated,
                selection,
                selected_contact.model_dump() if selected_contact is not None else None,
            )

        return await update(self._client.transaction())


def _snapshot_created_at(snapshot) -> datetime:
    created_at = getattr(snapshot, "create_time", None)
    if isinstance(created_at, datetime):
        return created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _pickup_response(
    commitment: Commitment,
    selection: Literal["no_pickup", "selected"],
    contact: dict[str, object] | None,
) -> PickupContactResponse:
    display_name = contact.get("display_name") if contact is not None else None
    return PickupContactResponse(
        commitment_id=commitment.id,
        version=commitment.version,
        selection=selection,
        display_name=display_name if isinstance(display_name, str) else None,
    )


__all__ = [
    "ActionAuditSource",
    "DashboardSource",
    "FirestoreProductRepository",
    "ProductRepository",
    "PickupVersionConflict",
]
