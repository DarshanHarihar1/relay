from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import Commitment, Edge
from app.domain.impact import TraversalDiagnosticCode
from app.services.impact_graph import DownstreamGraphWalker

USER_ID = "u1"
NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def edge(from_ref: str, to_ref: str, edge_id: str) -> Edge:
    return Edge(id=edge_id, from_ref=from_ref, to_ref=to_ref, relation="depends_on")


def commitment(commitment_id: str) -> Commitment:
    return Commitment(
        id=commitment_id,
        user_id=USER_ID,
        source_event_key=f"seed:{commitment_id}",
        summary=commitment_id,
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
    )


class FakeRelayRepository:
    def __init__(self, edges: list[Edge], known_commitment_ids: set[str]) -> None:
        self._edges = edges
        self._commitments = {commitment_id: commitment(commitment_id) for commitment_id in known_commitment_ids}

    async def get_commitment(self, *, user_id: str, commitment_id: str) -> Commitment | None:
        assert user_id == USER_ID
        return self._commitments.get(commitment_id)

    async def get_commitments(self, *, user_id: str, commitment_ids):
        return [c for cid in commitment_ids if (c := await self.get_commitment(user_id=user_id, commitment_id=cid))]

    async def list_outgoing_edges(self, *, user_id: str, from_id: str) -> list[Edge]:
        assert user_id == USER_ID
        return [e for e in self._edges if e.from_ref == from_id]


def make_graph(*, edges: list[Edge], commitment_ids: set[str] | None = None) -> FakeRelayRepository:
    referenced = {e.from_ref for e in edges} | {e.to_ref for e in edges}
    known = commitment_ids if commitment_ids is not None else referenced - {"absent"}
    return FakeRelayRepository(edges, known)


@pytest.mark.asyncio
async def test_walk_includes_only_sorted_directed_descendants() -> None:
    repository = make_graph(
        edges=[edge("flight", "pickup", "e2"), edge("flight", "dinner", "e1"), edge("other", "secret", "e3")]
    )
    result = await DownstreamGraphWalker(repository, USER_ID).walk("flight")
    assert result.commitment_ids == ("flight", "dinner", "pickup")
    assert [e.id for e in result.edges] == ["e1", "e2"]
    assert result.diagnostics == ()


@pytest.mark.asyncio
async def test_walk_terminates_a_cycle_and_records_missing_targets() -> None:
    repository = make_graph(
        edges=[edge("flight", "dinner", "e1"), edge("dinner", "flight", "e2"), edge("dinner", "absent", "e3")]
    )
    result = await DownstreamGraphWalker(repository, USER_ID).walk("flight")
    assert result.commitment_ids == ("flight", "dinner")
    assert result.diagnostics == (TraversalDiagnosticCode.CYCLE_DETECTED, TraversalDiagnosticCode.MISSING_TARGET)


@pytest.mark.asyncio
async def test_walk_raises_for_an_unknown_source_commitment() -> None:
    repository = make_graph(edges=[])
    with pytest.raises(ValueError):
        await DownstreamGraphWalker(repository, USER_ID).walk("nowhere")
