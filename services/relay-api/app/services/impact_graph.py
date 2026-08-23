from __future__ import annotations

from collections import deque

from app.contracts import Edge
from app.domain.impact import ImpactModel, TraversalDiagnosticCode
from app.repositories.relay_repository import RelayRepository


class ReachableSubgraph(ImpactModel):
    commitment_ids: tuple[str, ...]
    edges: tuple[Edge, ...]
    diagnostics: tuple[TraversalDiagnosticCode, ...]


class DownstreamGraphWalker:
    """Sorted breadth-first traversal of only directed downstream edges."""

    def __init__(self, repository: RelayRepository, user_id: str) -> None:
        self._repository = repository
        self._user_id = user_id

    async def walk(self, source_commitment_id: str) -> ReachableSubgraph:
        source = await self._repository.get_commitment(user_id=self._user_id, commitment_id=source_commitment_id)
        if source is None:
            raise ValueError(f"unknown commitment: {source_commitment_id}")
        visited = {source.id}
        queue: deque[str] = deque([source.id])
        edges: list[Edge] = []
        diagnostics: set[TraversalDiagnosticCode] = set()
        ordered = [source.id]
        while queue:
            from_id = queue.popleft()
            outgoing = await self._repository.list_outgoing_edges(user_id=self._user_id, from_id=from_id)
            for edge in sorted(outgoing, key=lambda item: item.id):
                target = await self._repository.get_commitment(user_id=self._user_id, commitment_id=edge.to_ref)
                if target is None:
                    diagnostics.add(TraversalDiagnosticCode.MISSING_TARGET)
                    continue
                edges.append(edge)
                if target.id in visited:
                    diagnostics.add(TraversalDiagnosticCode.CYCLE_DETECTED)
                    continue
                visited.add(target.id)
                ordered.append(target.id)
                queue.append(target.id)
        return ReachableSubgraph(
            commitment_ids=tuple(ordered),
            edges=tuple(edges),
            diagnostics=tuple(sorted(diagnostics, key=lambda code: code.value)),
        )
