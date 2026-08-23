from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from app.contracts import AuthorizationSnapshot, ContractModel


class FeasibilityStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    AT_RISK = "AT_RISK"
    VIOLATED = "VIOLATED"


class TraversalDiagnosticCode(StrEnum):
    CYCLE_DETECTED = "CYCLE_DETECTED"
    MISSING_TARGET = "MISSING_TARGET"
    ROUTE_UNKNOWN = "ROUTE_UNKNOWN"


class ActionKind(StrEnum):
    CALL_CONTACT = "CALL_CONTACT"
    CALL_VENUE = "CALL_VENUE"
    CALL_HOTEL = "CALL_HOTEL"
    CREATE_CALENDAR_HOLD = "CREATE_CALENDAR_HOLD"
    OPEN_UBER_HANDOFF = "OPEN_UBER_HANDOFF"


class PolicyDecision(StrEnum):
    AUTO = "AUTO"
    ASK = "ASK"
    NEVER = "NEVER"


class CandidateKind(StrEnum):
    KEEP_AS_IS = "KEEP_AS_IS"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"
    REPLACE_TRANSPORT = "REPLACE_TRANSPORT"
    CONFIRM_LATE_ARRIVAL = "CONFIRM_LATE_ARRIVAL"


class ImpactModel(ContractModel):
    """Every phase-03 planning record is immutable once constructed."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RouteSnapshot(ImpactModel):
    origin_place_id: str
    destination_place_id: str
    departure_at: datetime
    duration_minutes: int = Field(ge=0, le=24 * 60)
    fetched_at: datetime
    expires_at: datetime


class ConstraintTrace(ImpactModel):
    edge_id: str
    source_id: str
    target_id: str
    release_at: datetime
    route_minutes: int | None
    constrained_arrival_at: datetime | None
    status: FeasibilityStatus
    reason: str


class ImpactNode(ImpactModel):
    commitment_id: str
    effective_start: datetime
    effective_end: datetime
    earliest_feasible_start: datetime | None
    latest_start: datetime
    slack_minutes: int | None
    status: FeasibilityStatus
    constraint_traces: tuple[ConstraintTrace, ...]


class ImpactAssessment(ImpactModel):
    id: str
    disruption_id: str
    source_commitment_id: str
    reachable_commitment_ids: tuple[str, ...]
    affected_commitment_ids: tuple[str, ...]
    nodes: tuple[ImpactNode, ...]
    diagnostics: tuple[TraversalDiagnosticCode, ...]
    input_fingerprint: str


class CandidateChange(ImpactModel):
    commitment_id: str
    kind: CandidateKind
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    financial_cost_inr: int = Field(default=0, ge=0)
    social_coordination_units: int = Field(default=0, ge=0)
    preference_violation_units: int = Field(default=0, ge=0)
    action_kinds: tuple[ActionKind, ...] = ()
    # Not in the original plan text: Task 7 needs to know who an action_kind's
    # call or handoff targets, and the plan never carried that from
    # RescheduleOption/TransportOption.target_ref through to here.
    target_ref: str | None = None


class RepairScore(ImpactModel):
    invariant_violations: int = Field(ge=0)
    missed_critical_commitments: int = Field(ge=0)
    changed_commitments: int = Field(ge=0)
    financial_cost_inr: int = Field(ge=0)
    social_coordination_units: int = Field(ge=0)
    preference_violation_units: int = Field(ge=0)
    avoidable_delay_minutes: int = Field(ge=0)
    weighted_total: int = Field(ge=0)

    def sort_key(self, candidate_id: str) -> tuple[int, int, int, int, int, int, int, str]:
        return (
            self.invariant_violations,
            self.missed_critical_commitments,
            self.changed_commitments,
            self.financial_cost_inr,
            self.social_coordination_units,
            self.preference_violation_units,
            self.avoidable_delay_minutes,
            candidate_id,
        )


class RepairCandidate(ImpactModel):
    id: str
    kind: CandidateKind
    changes: tuple[CandidateChange, ...]
    score: RepairScore | None = None
    invalid_reasons: tuple[str, ...]
    explanation: str
    projected_nodes: tuple[ImpactNode, ...] = ()


class RepairPlan(ImpactModel):
    id: str
    version: int = Field(ge=1)
    assessment_id: str
    selected_candidate_id: str | None
    candidates: tuple[RepairCandidate, ...]
    # Phase 3 produces the canonical `Approval` record (see app/contracts.py),
    # not a second "ApprovalBatch" type. None when no approval was required
    # or the plan is blocked (see the reconciliation ledger, Finding 3).
    approval_id: str | None
    input_fingerprint: str


class RescheduleOption(ImpactModel):
    commitment_id: str
    start: datetime
    end: datetime
    target_ref: str
    max_fee_inr: int = Field(ge=0)


class TransportOption(ImpactModel):
    commitment_id: str
    start: datetime
    end: datetime
    target_ref: str
    cost_inr: int = Field(ge=0)


class PlanningOptions(ImpactModel):
    reschedule_options: tuple[RescheduleOption, ...] = ()
    transport_options: tuple[TransportOption, ...] = ()
    cancellable_commitment_ids: tuple[str, ...] = ()
    late_arrival_target_refs: Mapping[str, str] = Field(default_factory=dict)
    approval_expires_at: datetime


def _canonical_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (tuple, set, frozenset)):
        return list(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Object of type {type(value).__name__} is not canonically serializable")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        default=_canonical_default,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def sha256_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"


def make_assessment_id(disruption_id: str, input_fingerprint: str) -> str:
    return sha256_id("assessment", {"disruption_id": disruption_id, "input_fingerprint": input_fingerprint})


def make_repair_plan_id(assessment_id: str, version: int) -> str:
    return sha256_id("plan", {"assessment_id": assessment_id, "version": version})


def make_action_record_id(
    repair_plan_id: str,
    candidate_id: str,
    kind: ActionKind,
    target_ref: str,
) -> str:
    return sha256_id(
        "action",
        {
            "repair_plan_id": repair_plan_id,
            "candidate_id": candidate_id,
            "kind": kind,
            "target_ref": target_ref,
        },
    )


def make_action_idempotency_key(
    repair_plan_version: int,
    kind: ActionKind,
    target_ref: str,
    authorization_snapshot: AuthorizationSnapshot,
) -> str:
    return sha256_id(
        "akey",
        {
            "repair_plan_version": repair_plan_version,
            "kind": kind,
            "target_ref": target_ref,
            "authorization_snapshot": authorization_snapshot,
        },
    )
