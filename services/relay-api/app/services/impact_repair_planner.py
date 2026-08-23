from __future__ import annotations

from app.contracts import Disruption
from app.domain.impact import (
    FeasibilityStatus,
    ImpactAssessment,
    ImpactNode,
    PlanningOptions,
    RepairPlan,
    TraversalDiagnosticCode,
    make_assessment_id,
    make_repair_plan_id,
    sha256_id,
)
from app.repositories.relay_repository import RelayRepository
from app.services.feasibility import FeasibilityEngine
from app.services.impact_graph import DownstreamGraphWalker
from app.services.repair_candidates import CandidateFactory, candidate_is_feasible, score_candidate, select_candidate
from app.services.repair_policy import PolicyEngine, UserActionPolicy


class ImpactRepairPlanner:
    """Coordinates the pure phase-03 services and persists idempotently.

    Never calls a provider adapter; the returned plan authorizes at most one
    already-safe candidate and produces no dispatched action.
    """

    def __init__(
        self,
        repository: RelayRepository,
        user_id: str,
        walker: DownstreamGraphWalker,
        feasibility: FeasibilityEngine,
        candidates: CandidateFactory,
        policy: PolicyEngine,
    ) -> None:
        self._repository = repository
        self._user_id = user_id
        self._walker = walker
        self._feasibility = feasibility
        self._candidates = candidates
        self._policy = policy

    async def create_plan(
        self,
        disruption: Disruption,
        options: PlanningOptions,
        user_policy: UserActionPolicy,
    ) -> RepairPlan:
        if not disruption.commitment_id:
            raise ValueError("A disruption must be linked to a commitment before planning")

        subgraph = await self._walker.walk(disruption.commitment_id)
        commitments = {
            item.id: item
            for item in await self._repository.get_commitments(
                user_id=self._user_id, commitment_ids=subgraph.commitment_ids
            )
        }
        fingerprint = sha256_id(
            "ifp",
            {
                "disruption": disruption,
                "commitments": commitments,
                "edges": subgraph.edges,
                "options": options,
                "policy": user_policy,
            },
        )
        existing = await self._repository.get_repair_plan_by_fingerprint(
            user_id=self._user_id, disruption_id=disruption.id, input_fingerprint=fingerprint
        )
        if existing is not None:
            return existing

        nodes, feasibility_diagnostics = await self._feasibility.assess(disruption, commitments, subgraph)
        assessment = _build_assessment(disruption, subgraph, nodes, feasibility_diagnostics, fingerprint)

        evaluated = []
        for candidate in self._candidates.generate(assessment, commitments, options):
            overrides = {
                change.commitment_id: (change.proposed_start, change.proposed_end)
                for change in candidate.changes
                if change.proposed_start is not None and change.proposed_end is not None
            }
            projected_nodes, _ = await self._feasibility.assess(disruption, commitments, subgraph, overrides)
            scored = candidate.model_copy(update={"projected_nodes": projected_nodes})
            scored = scored.model_copy(update={"score": score_candidate(scored, assessment, commitments)})
            evaluated.append(scored)

        # candidate_is_feasible() also rejects a candidate whose projected
        # nodes are still VIOLATED, which validate_candidate() alone (the
        # source of score.invariant_violations) does not catch.
        feasible = tuple(c for c in evaluated if candidate_is_feasible(c, assessment, commitments))
        selected = select_candidate(feasible)

        repair_plan_id = make_repair_plan_id(assessment.id, 1)
        action_records = ()
        approval = None
        if selected is not None:
            action_records, approval = self._policy.create_batch(
                user_id=self._user_id,
                repair_plan_id=repair_plan_id,
                repair_plan_version=1,
                candidate=selected,
                policy=user_policy,
                expires_at=options.approval_expires_at,
                correlation_id=disruption.correlation_id or disruption.id,
            )

        plan = RepairPlan(
            id=repair_plan_id,
            version=1,
            assessment_id=assessment.id,
            selected_candidate_id=selected.id if selected is not None else None,
            candidates=tuple(evaluated),
            approval_id=approval.id if approval is not None else None,
            input_fingerprint=fingerprint,
        )

        return await self._repository.save_planning_result(
            user_id=self._user_id,
            assessment=assessment,
            plan=plan,
            action_records=action_records,
            approval=approval,
        )


def _build_assessment(
    disruption: Disruption,
    subgraph,
    nodes: tuple[ImpactNode, ...],
    feasibility_diagnostics: tuple[TraversalDiagnosticCode, ...],
    fingerprint: str,
) -> ImpactAssessment:
    diagnostics = tuple(
        sorted(set(subgraph.diagnostics) | set(feasibility_diagnostics), key=lambda code: code.value)
    )
    return ImpactAssessment(
        id=make_assessment_id(disruption.id, fingerprint),
        disruption_id=disruption.id,
        source_commitment_id=disruption.commitment_id,
        reachable_commitment_ids=subgraph.commitment_ids,
        affected_commitment_ids=tuple(
            node.commitment_id for node in nodes if node.status is not FeasibilityStatus.FEASIBLE
        ),
        nodes=nodes,
        diagnostics=diagnostics,
        input_fingerprint=fingerprint,
    )
