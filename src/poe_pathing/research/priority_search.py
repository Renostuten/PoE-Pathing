"""Priority-guided bounded search for Problem A experiments.

The strategy uses an optimistic reward bound to decide which state to expand
next.  The bound is deliberately loose: it adds the globally highest positive
node scores that could fit in the remaining point budget while ignoring
connectivity and whether those nodes have already appeared in the path.  This
is a safe upper bound on additional additive reward, but it is used only for
heap ordering.

Per-bucket beam limits and the global expansion cap make this a heuristic
search.  A completed run must not be described as exact merely because the
frontier happened to empty before the global cap.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import heapq
from typing import Iterable, Mapping

from .exact_solver import ExactPathCandidate


DesiredStats = Mapping[tuple[str, str], float]


@dataclass(frozen=True, slots=True)
class PrioritySearchConfig:
    """Resource limits for the bounded experimental strategy."""

    states_per_bucket: int = 4
    max_expanded_states: int = 50_000
    candidate_pool_size: int = 350

    def __post_init__(self) -> None:
        if self.states_per_bucket <= 0:
            raise ValueError("states_per_bucket must be positive")
        if self.max_expanded_states <= 0:
            raise ValueError("max_expanded_states must be positive")
        if self.candidate_pool_size <= 0:
            raise ValueError("candidate_pool_size must be positive")


@dataclass(frozen=True, slots=True)
class PrioritySearchDiagnostics:
    """Counters that make every source of bounded behaviour visible."""

    expanded_states: int
    generated_states: int
    candidate_states: int
    returned_candidates: int
    dominance_pruned_states: int
    beam_pruned_states: int
    stale_states: int
    truncated: bool
    candidate_pool_truncated: bool


@dataclass(frozen=True, slots=True)
class PrioritySearchResult:
    """Ranked candidates from a heuristic priority-guided search."""

    candidates: tuple[ExactPathCandidate, ...]
    diagnostics: PrioritySearchDiagnostics

    @property
    def best(self) -> ExactPathCandidate | None:
        return self.candidates[0] if self.candidates else None

    def top(self, limit: int) -> tuple[ExactPathCandidate, ...]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        return self.candidates[:limit]


@dataclass(frozen=True, slots=True)
class _PriorityState:
    state_id: int
    current: str
    path: tuple[str, ...]
    visited: frozenset[str]
    cost: int
    score: float
    optimistic_bound: float


class OptimisticPrioritySearch:
    """Deterministic optimistic-bound beam search for single paths.

    States are bucketed by ``(current node, point cost)``.  Safe subset
    dominance is applied first, then at most ``states_per_bucket`` states are
    retained by current score, future flexibility, and lexical path order.
    The latter beam step and the expansion cap can discard the optimum.

    Positive prefixes are not removed merely because a longer candidate
    exists.  All explored positive candidates compete under the current
    score-first, efficiency-second objective before the candidate pool is
    truncated.
    """

    def __init__(
        self,
        pathfinder,
        path_evaluator,
        config: PrioritySearchConfig | None = None,
    ) -> None:
        self.pathfinder = pathfinder
        self.path_evaluator = path_evaluator
        self.config = config or PrioritySearchConfig()

    def search(
        self,
        allocated: Iterable[str],
        desired_stats: DesiredStats,
        max_points: int = 10,
    ) -> PrioritySearchResult:
        if max_points < 0:
            raise ValueError("max_points must be non-negative")

        allocated_set = frozenset(allocated)
        node_scores = self._score_graph_nodes(
            allocated_set,
            desired_stats,
        )
        positive_score_prefix = self._positive_score_prefix(node_scores)

        buckets: dict[
            tuple[str, int],
            list[_PriorityState],
        ] = defaultdict(list)
        retained_state_ids: set[int] = set()
        frontier: list[
            tuple[
                float,
                float,
                float,
                tuple[str, ...],
                int,
                _PriorityState,
            ]
        ] = []
        candidates: list[ExactPathCandidate] = []

        next_state_id = 0
        expanded_states = 0
        generated_states = 0
        dominance_pruned_states = 0
        beam_pruned_states = 0
        stale_states = 0
        truncated = False

        def make_state(
            *,
            current: str,
            path: tuple[str, ...],
            visited: frozenset[str],
            cost: int,
            score: float,
        ) -> _PriorityState:
            nonlocal next_state_id
            state = _PriorityState(
                state_id=next_state_id,
                current=current,
                path=path,
                visited=visited,
                cost=cost,
                score=score,
                optimistic_bound=self._optimistic_bound(
                    score,
                    max_points - cost,
                    positive_score_prefix,
                ),
            )
            next_state_id += 1
            return state

        def offer(state: _PriorityState) -> None:
            nonlocal generated_states
            nonlocal dominance_pruned_states
            nonlocal beam_pruned_states

            generated_states += 1
            key = (state.current, state.cost)
            bucket = buckets[key]

            if any(
                self._dominates(existing, state)
                for existing in bucket
            ):
                dominance_pruned_states += 1
                return

            survivors: list[_PriorityState] = []
            for existing in bucket:
                if self._dominates(state, existing):
                    retained_state_ids.discard(existing.state_id)
                    dominance_pruned_states += 1
                else:
                    survivors.append(existing)

            survivors.append(state)
            survivors.sort(key=self._bucket_order)
            retained = survivors[: self.config.states_per_bucket]
            evicted = survivors[self.config.states_per_bucket :]
            buckets[key] = retained

            for evicted_state in evicted:
                retained_state_ids.discard(evicted_state.state_id)
                beam_pruned_states += 1

            if state not in retained:
                return

            retained_state_ids.add(state.state_id)
            heapq.heappush(frontier, self._heap_entry(state))

        for node_id in sorted(allocated_set):
            offer(
                make_state(
                    current=node_id,
                    path=(node_id,),
                    visited=frozenset({node_id}),
                    cost=0,
                    score=0.0,
                )
            )

        while frontier:
            *_, state = heapq.heappop(frontier)
            if state.state_id not in retained_state_ids:
                stale_states += 1
                continue

            if expanded_states >= self.config.max_expanded_states:
                truncated = True
                break

            expanded_states += 1
            if state.cost > 0 and state.score > 0:
                candidates.append(
                    ExactPathCandidate(
                        path=state.path,
                        cost=state.cost,
                        score=state.score,
                        efficiency=state.score / state.cost,
                    )
                )

            for neighbour in sorted(
                set(self.pathfinder.adj.get(state.current, ()))
            ):
                if neighbour in state.visited:
                    continue
                if not self._is_traversable(neighbour, allocated_set):
                    continue

                is_allocated = neighbour in allocated_set
                next_cost = state.cost + (0 if is_allocated else 1)
                if next_cost > max_points:
                    continue

                next_score = state.score
                if not is_allocated:
                    next_score += node_scores[neighbour]

                offer(
                    make_state(
                        current=neighbour,
                        path=(*state.path, neighbour),
                        visited=state.visited | {neighbour},
                        cost=next_cost,
                        score=next_score,
                    )
                )

        candidates.sort(
            key=lambda candidate: (
                -candidate.score,
                -candidate.efficiency,
                candidate.path,
            )
        )
        candidate_pool_truncated = (
            len(candidates) > self.config.candidate_pool_size
        )
        returned_candidates = tuple(
            candidates[: self.config.candidate_pool_size]
        )
        diagnostics = PrioritySearchDiagnostics(
            expanded_states=expanded_states,
            generated_states=generated_states,
            candidate_states=len(candidates),
            returned_candidates=len(returned_candidates),
            dominance_pruned_states=dominance_pruned_states,
            beam_pruned_states=beam_pruned_states,
            stale_states=stale_states,
            truncated=truncated,
            candidate_pool_truncated=candidate_pool_truncated,
        )
        return PrioritySearchResult(
            candidates=returned_candidates,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _dominates(
        left: _PriorityState,
        right: _PriorityState,
    ) -> bool:
        if left.score < right.score:
            return False
        if not left.visited.issubset(right.visited):
            return False

        # When future possibilities and score are identical, retain the
        # lexicographically smaller representative for deterministic ties.
        if (
            left.score == right.score
            and left.visited == right.visited
            and left.path > right.path
        ):
            return False
        return True

    @staticmethod
    def _bucket_order(
        state: _PriorityState,
    ) -> tuple[float, int, tuple[str, ...]]:
        return (-state.score, len(state.visited), state.path)

    @staticmethod
    def _heap_entry(
        state: _PriorityState,
    ) -> tuple[
        float,
        float,
        float,
        tuple[str, ...],
        int,
        _PriorityState,
    ]:
        efficiency = state.score / state.cost if state.cost > 0 else 0.0
        return (
            -state.optimistic_bound,
            -state.score,
            -efficiency,
            state.path,
            state.state_id,
            state,
        )

    def _score_graph_nodes(
        self,
        allocated: frozenset[str],
        desired_stats: DesiredStats,
    ) -> dict[str, float]:
        node_ids = set(self.pathfinder.adj)
        for neighbours in self.pathfinder.adj.values():
            node_ids.update(neighbours)

        return {
            node_id: (
                0.0
                if node_id in allocated
                else self.path_evaluator.node_scorer.score_node(
                    node_id,
                    desired_stats,
                )
            )
            for node_id in sorted(node_ids)
        }

    @staticmethod
    def _positive_score_prefix(
        node_scores: Mapping[str, float],
    ) -> tuple[float, ...]:
        positive_scores = sorted(
            (score for score in node_scores.values() if score > 0),
            reverse=True,
        )
        prefix = [0.0]
        for score in positive_scores:
            prefix.append(prefix[-1] + score)
        return tuple(prefix)

    @staticmethod
    def _optimistic_bound(
        score: float,
        remaining_points: int,
        positive_score_prefix: tuple[float, ...],
    ) -> float:
        usable_scores = min(
            remaining_points,
            len(positive_score_prefix) - 1,
        )
        return score + positive_score_prefix[usable_scores]

    def _is_traversable(
        self,
        node_id: str,
        allocated: frozenset[str],
    ) -> bool:
        public_method = getattr(self.pathfinder, "is_traversable", None)
        if callable(public_method):
            return bool(public_method(node_id, allocated))

        private_method = getattr(self.pathfinder, "_is_traversable", None)
        if not callable(private_method):
            raise TypeError(
                "pathfinder must provide is_traversable(node_id, allocated)"
            )
        return bool(private_method(node_id, allocated))
