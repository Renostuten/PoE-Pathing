"""Faithful, instrumented probe for the current production search.

The probe deliberately subclasses :class:`TreeOptimizer` and replaces only
``find_candidate_paths``.  Its control flow mirrors the production method,
    including LIFO traversal, set-order seeding, insertion-order adjacency,
    bucket retention, queued
states that remain expandable after bucket eviction, and the post-increment
global-cap check.  It is research instrumentation, not another search
strategy and is not wired into the application container.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..calculation.tree_optimizer import TreeOptimizer


DesiredStats = Mapping[tuple[str, str], float]


@dataclass(frozen=True, slots=True)
class CurrentSearchDiagnostics:
    """Immutable counters for one production-equivalent candidate search.

    ``generated_states`` includes the set-ordered allocated seeds.  Production
    increments its expanded counter immediately after popping a state and
    checks the cap afterwards, so a capped run has one more expanded than
    processed state.  Dominance and width pruning count bucket-retention
    removals; an already queued state is intentionally not removed from the
    LIFO frontier and may subsequently be processed.
    """

    seeded_states: int
    generated_states: int
    expanded_states: int
    processed_states: int
    candidate_states: int
    returned_candidate_states: int
    dominance_pruned_states: int
    dominance_rejected_states: int
    dominance_evicted_states: int
    bucket_width_pruned_states: int
    bucket_width_rejected_states: int
    bucket_width_evicted_states: int
    evicted_while_queued_states: int
    processed_after_bucket_eviction_states: int
    cap_truncated: bool
    cap_discarded_states: int
    candidate_pool_truncated: bool
    candidate_pool_discarded_states: int


@dataclass(frozen=True, slots=True)
class CurrentSearchProbeResult:
    """Production-shaped recommendations plus raw search diagnostics."""

    candidates: tuple[dict[str, object], ...]
    recommendations: tuple[dict[str, object], ...]
    diagnostics: CurrentSearchDiagnostics


@dataclass(slots=True)
class _MutableCounters:
    seeded_states: int = 0
    generated_states: int = 0
    expanded_states: int = 0
    processed_states: int = 0
    candidate_states: int = 0
    returned_candidate_states: int = 0
    dominance_pruned_states: int = 0
    dominance_rejected_states: int = 0
    dominance_evicted_states: int = 0
    bucket_width_pruned_states: int = 0
    bucket_width_rejected_states: int = 0
    bucket_width_evicted_states: int = 0
    evicted_while_queued_states: int = 0
    processed_after_bucket_eviction_states: int = 0
    cap_truncated: bool = False
    cap_discarded_states: int = 0
    candidate_pool_truncated: bool = False
    candidate_pool_discarded_states: int = 0

    def freeze(self) -> CurrentSearchDiagnostics:
        return CurrentSearchDiagnostics(
            **{
                field: getattr(self, field)
                for field in CurrentSearchDiagnostics.__dataclass_fields__
            }
        )


class CurrentSearchProbe(TreeOptimizer):
    """Instrument the current bounded search without changing its output."""

    def __init__(self, pathfinder, path_evaluator) -> None:
        super().__init__(pathfinder, path_evaluator)
        self._last_diagnostics: CurrentSearchDiagnostics | None = None
        self._last_candidates: tuple[dict[str, object], ...] = ()

    @property
    def last_diagnostics(self) -> CurrentSearchDiagnostics | None:
        """Diagnostics from the most recent candidate search, if any."""

        return self._last_diagnostics

    @property
    def last_candidates(self) -> tuple[dict[str, object], ...]:
        """A defensive snapshot of the most recent pooled candidates."""

        return deepcopy(self._last_candidates)

    def recommend_paths_with_diagnostics(
        self,
        allocated: Iterable[str],
        desired_stats: DesiredStats,
        max_points: int = 10,
        limit: int = 10,
    ) -> CurrentSearchProbeResult:
        """Run inherited recommendation post-processing and return its trace."""

        recommendations = super().recommend_paths(
            allocated,
            desired_stats,
            max_points,
            limit,
        )
        if self._last_diagnostics is None:  # pragma: no cover - defensive
            raise RuntimeError("candidate search produced no diagnostics")
        return CurrentSearchProbeResult(
            candidates=self.last_candidates,
            recommendations=tuple(deepcopy(recommendations)),
            diagnostics=self._last_diagnostics,
        )

    def find_candidate_paths(
        self,
        allocated,
        desired_stats: DesiredStats,
        max_points,
    ):
        """Mirror ``TreeOptimizer.find_candidate_paths`` with counters."""

        allocated = set(allocated)
        buckets = defaultdict(list)
        candidates = []
        frontier = []
        node_scores = {}
        counters = _MutableCounters()
        queued_state_ids: set[int] = set()
        evicted_queued_state_ids: set[int] = set()

        for node_id in allocated:
            state = {
                "current": node_id,
                "path": [node_id],
                "visited": frozenset({node_id}),
                "cost": 0,
                "score": 0.0,
            }
            buckets[(node_id, 0)].append(state)
            frontier.append(state)
            queued_state_ids.add(id(state))
            counters.seeded_states += 1
            counters.generated_states += 1

        while frontier:
            state = frontier.pop()
            state_id = id(state)
            queued_state_ids.discard(state_id)
            was_evicted_while_queued = (
                state_id in evicted_queued_state_ids
            )
            evicted_queued_state_ids.discard(state_id)
            counters.expanded_states += 1

            if counters.expanded_states > self.MAX_EXPANDED_STATES:
                counters.cap_truncated = True
                counters.cap_discarded_states = len(frontier) + 1
                break

            counters.processed_states += 1
            if was_evicted_while_queued:
                counters.processed_after_bucket_eviction_states += 1

            if state["cost"] > 0 and state["score"] > 0:
                candidates.append(self.build_candidate(state))

            if state["cost"] >= max_points:
                continue

            for neighbour in self.pathfinder.adj.get(
                state["current"], []
            ):
                if neighbour in state["visited"]:
                    continue

                if not self.pathfinder._is_traversable(
                    neighbour, allocated
                ):
                    continue

                is_allocated = neighbour in allocated
                next_cost = state["cost"] + (0 if is_allocated else 1)

                if next_cost > max_points:
                    continue

                next_score = state["score"]
                if not is_allocated:
                    if neighbour not in node_scores:
                        node_scores[neighbour] = (
                            self.path_evaluator.node_scorer.score_node(
                                neighbour, desired_stats
                            )
                        )
                    next_score += node_scores[neighbour]

                next_state = {
                    "current": neighbour,
                    "path": [*state["path"], neighbour],
                    "visited": frozenset(
                        {*state["visited"], neighbour}
                    ),
                    "cost": next_cost,
                    "score": next_score,
                }
                counters.generated_states += 1

                if self._keep_state_with_diagnostics(
                    next_state,
                    buckets,
                    counters,
                    queued_state_ids,
                    evicted_queued_state_ids,
                ):
                    frontier.append(next_state)
                    queued_state_ids.add(id(next_state))

        candidates.sort(
            key=lambda candidate: (
                candidate["score"], candidate["efficiency"]
            ),
            reverse=True,
        )
        counters.candidate_states = len(candidates)
        pooled_candidates = candidates[: self.CANDIDATE_POOL_SIZE]
        counters.returned_candidate_states = len(pooled_candidates)
        counters.candidate_pool_discarded_states = (
            len(candidates) - len(pooled_candidates)
        )
        counters.candidate_pool_truncated = (
            counters.candidate_pool_discarded_states > 0
        )

        self._last_candidates = tuple(deepcopy(pooled_candidates))
        self._last_diagnostics = counters.freeze()
        return pooled_candidates

    def _keep_state_with_diagnostics(
        self,
        state,
        buckets,
        counters: _MutableCounters,
        queued_state_ids: set[int],
        evicted_queued_state_ids: set[int],
    ) -> bool:
        """Mirror ``keep_state`` while observing bucket removals."""

        key = (state["current"], state["cost"])
        bucket = buckets[key]

        for existing in bucket:
            if (
                existing["score"] >= state["score"]
                and existing["visited"].issubset(state["visited"])
            ):
                counters.dominance_pruned_states += 1
                counters.dominance_rejected_states += 1
                return False

        survivors = []
        for existing in bucket:
            if (
                state["score"] >= existing["score"]
                and state["visited"].issubset(existing["visited"])
            ):
                counters.dominance_pruned_states += 1
                counters.dominance_evicted_states += 1
                self._record_queued_eviction(
                    existing,
                    counters,
                    queued_state_ids,
                    evicted_queued_state_ids,
                )
            else:
                survivors.append(existing)

        buckets[key] = survivors
        buckets[key].append(state)
        buckets[key].sort(
            key=lambda item: item["score"], reverse=True
        )

        if len(buckets[key]) > self.STATES_PER_BUCKET:
            evicted = buckets[key][self.STATES_PER_BUCKET :]
            del buckets[key][self.STATES_PER_BUCKET :]
            counters.bucket_width_pruned_states += len(evicted)
            for evicted_state in evicted:
                if evicted_state is state:
                    counters.bucket_width_rejected_states += 1
                else:
                    counters.bucket_width_evicted_states += 1
                self._record_queued_eviction(
                    evicted_state,
                    counters,
                    queued_state_ids,
                    evicted_queued_state_ids,
                )

        # This intentionally uses equality membership, exactly like production.
        return state in buckets[key]

    @staticmethod
    def _record_queued_eviction(
        state,
        counters: _MutableCounters,
        queued_state_ids: set[int],
        evicted_queued_state_ids: set[int],
    ) -> None:
        state_id = id(state)
        if (
            state_id in queued_state_ids
            and state_id not in evicted_queued_state_ids
        ):
            counters.evicted_while_queued_states += 1
            evicted_queued_state_ids.add(state_id)
