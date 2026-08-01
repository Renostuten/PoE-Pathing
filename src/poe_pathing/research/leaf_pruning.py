"""Query-specific non-useful-leaf pruning for research experiments.

The utility in this module repeatedly removes an active graph node only when
it is unallocated, is not required, has current degree zero or one, has a
non-positive query score, and is permitted by the caller's special-removal
predicate.  It is deliberately separate from the production optimiser.

For Problem A's additive score, an at-most point budget, and score-first then
efficiency ranking, removing such an endpoint preserves the value of the best
recommendation: truncating a path before a non-positive leaf never lowers its
score and never increases its point cost.  Applying the same argument after
each removal proves the repeated peel by induction.

That guarantee does *not* preserve the complete candidate collection or its
top-k diversity.  A positive prefix extended by a zero-score leaf is a
distinct candidate in the unpruned graph.  Nor is the rule safe for an exact
point-budget objective, a required endpoint, or an unmodelled traversal rule.
Callers must protect fixed nodes with ``required_nodes`` and reject removal of
special node types with ``can_remove``.  The predicate must be deterministic
and depend only on query-static information.

The queue peel itself visits each node and edge a constant number of times,
so it is ``O(|V| + |E|)``.  Canonicalising arbitrary input and materialising a
lexicographically sorted result add the expected comparison-sorting overhead.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Iterable, Mapping


RemovalPredicate = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class LeafRemoval:
    """One deterministic removal and its zero-based parallel peel round."""

    node_id: str
    peel_round: int


@dataclass(frozen=True, slots=True)
class LeafPruningDiagnostics:
    """Immutable structural counters for one pruning run.

    ``enqueued_node_count`` equals ``removed_node_count`` for a deterministic,
    query-static predicate because a removable node stays removable as graph
    degrees decrease.  Both fields are retained so benchmark output makes the
    queue behaviour and actual removals explicit.
    """

    original_node_count: int
    original_edge_count: int
    remaining_node_count: int
    remaining_edge_count: int
    initial_queue_size: int
    enqueued_node_count: int
    removed_node_count: int
    removals: tuple[LeafRemoval, ...]

    @property
    def removed_edge_count(self) -> int:
        """Return the number of undirected edges eliminated by the peel."""

        return self.original_edge_count - self.remaining_edge_count

    @property
    def removed_node_percentage(self) -> float:
        """Return the percentage of original graph nodes removed."""

        if self.original_node_count == 0:
            return 0.0
        return 100.0 * self.removed_node_count / self.original_node_count

    @property
    def removed_order(self) -> tuple[str, ...]:
        """Return node identifiers in deterministic queue-removal order."""

        return tuple(removal.node_id for removal in self.removals)

    @property
    def max_peel_round(self) -> int | None:
        """Return the deepest zero-based peel round, or ``None`` if unchanged."""

        if not self.removals:
            return None
        return max(removal.peel_round for removal in self.removals)


@dataclass(frozen=True, slots=True)
class LeafPruningResult:
    """A read-only reduced adjacency mapping and immutable diagnostics."""

    adjacency: Mapping[str, tuple[str, ...]]
    diagnostics: LeafPruningDiagnostics

    @property
    def reduced_adjacency(self) -> Mapping[str, tuple[str, ...]]:
        """Alias that makes experimental call sites self-documenting."""

        return self.adjacency

    @property
    def removed_order(self) -> tuple[str, ...]:
        """Return the deterministic removal order."""

        return self.diagnostics.removed_order


def prune_non_useful_leaves(
    adjacency: Mapping[str, Iterable[str]],
    node_scores: Mapping[str, float],
    allocated: Iterable[str],
    *,
    required_nodes: Iterable[str] = (),
    can_remove: RemovalPredicate | None = None,
) -> LeafPruningResult:
    """Repeatedly remove safe non-positive leaves for one scoring query.

    ``adjacency`` is interpreted as an undirected edge description.  Reverse
    entries are filled in, duplicate edges are ignored, nodes appearing only
    as neighbours are retained, and explicit isolated keys remain part of the
    graph unless they themselves satisfy the removal rule.  Self-loops are
    rejected because degree-one leaf semantics would otherwise be ambiguous.

    Missing node scores are treated as zero.  ``can_remove`` defaults to
    permitting removal; callers with mastery, proxy, ascendancy, jewel,
    class-start, or other special traversal semantics must protect those
    nodes through the predicate or ``required_nodes``.
    """

    protected = frozenset(allocated) | frozenset(required_nodes)
    removal_allowed = can_remove or _allow_removal
    active_neighbours = _normalise_undirected(adjacency)
    original_node_count = len(active_neighbours)
    original_edge_count = sum(
        len(neighbours) for neighbours in active_neighbours.values()
    ) // 2

    queued: set[str] = set()
    queue: deque[tuple[str, int]] = deque()

    def enqueue_if_removable(node_id: str, peel_round: int) -> None:
        if node_id in queued:
            return
        if len(active_neighbours[node_id]) > 1:
            return
        if node_id in protected:
            return
        if node_scores.get(node_id, 0.0) > 0:
            return
        if not removal_allowed(node_id):
            return

        queued.add(node_id)
        queue.append((node_id, peel_round))

    for node_id in sorted(active_neighbours):
        enqueue_if_removable(node_id, 0)

    initial_queue_size = len(queue)
    active_nodes = set(active_neighbours)
    removals: list[LeafRemoval] = []

    while queue:
        node_id, peel_round = queue.popleft()
        if node_id not in active_nodes:
            continue

        # Degree only decreases, so a node satisfying the query-static rule
        # when queued remains removable by the time it reaches the front.
        neighbours = tuple(active_neighbours[node_id])
        active_nodes.remove(node_id)
        removals.append(LeafRemoval(node_id, peel_round))

        for neighbour in neighbours:
            active_neighbours[neighbour].remove(node_id)
            enqueue_if_removable(neighbour, peel_round + 1)
        active_neighbours[node_id].clear()

    reduced = {
        node_id: tuple(
            sorted(
                neighbour
                for neighbour in active_neighbours[node_id]
                if neighbour in active_nodes
            )
        )
        for node_id in sorted(active_nodes)
    }
    remaining_edge_count = sum(map(len, reduced.values())) // 2
    immutable_adjacency = MappingProxyType(reduced)
    immutable_removals = tuple(removals)

    return LeafPruningResult(
        adjacency=immutable_adjacency,
        diagnostics=LeafPruningDiagnostics(
            original_node_count=original_node_count,
            original_edge_count=original_edge_count,
            remaining_node_count=len(reduced),
            remaining_edge_count=remaining_edge_count,
            initial_queue_size=initial_queue_size,
            enqueued_node_count=len(queued),
            removed_node_count=len(immutable_removals),
            removals=immutable_removals,
        ),
    )


def _normalise_undirected(
    adjacency: Mapping[str, Iterable[str]],
) -> dict[str, set[str]]:
    normalised = {node_id: set() for node_id in adjacency}

    for node_id, neighbours in adjacency.items():
        for neighbour in neighbours:
            if neighbour == node_id:
                raise ValueError(
                    "leaf pruning requires a loop-free undirected graph"
                )
            normalised.setdefault(node_id, set()).add(neighbour)
            normalised.setdefault(neighbour, set()).add(node_id)

    return normalised


def _allow_removal(node_id: str) -> bool:
    del node_id
    return True


__all__ = [
    "LeafPruningDiagnostics",
    "LeafPruningResult",
    "LeafRemoval",
    "RemovalPredicate",
    "prune_non_useful_leaves",
]
