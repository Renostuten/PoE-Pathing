"""Exact, deliberately small-scale baseline for single-path recommendations.

This module solves Problem A from ``AGENTS.md``.  It enumerates every valid
simple path rooted at an allocated node, subject to the point budget.  It does
not choose a connected, branching passive subtree.

The implementation intentionally favours auditability over performance.  A
default expanded-state limit protects callers from combinatorial blow-ups.  If
that limit is reached, :class:`ExactSearchLimitExceeded` is raised; a partial
search is never returned as an exact result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


DesiredStats = Mapping[tuple[str, str], float]


@dataclass(frozen=True, slots=True)
class ExactPathCandidate:
    """A positive-score path extension ranked using current score semantics."""

    path: tuple[str, ...]
    cost: int
    score: float
    efficiency: float

    @property
    def target(self) -> str:
        return self.path[-1]

    def as_dict(self) -> dict[str, object]:
        """Return the fields shared with ``TreeOptimizer`` candidates."""

        return {
            "target": self.target,
            "path": list(self.path),
            "cost": self.cost,
            "score": self.score,
            "efficiency": self.efficiency,
        }


@dataclass(frozen=True, slots=True)
class ExactSearchDiagnostics:
    """Counters for an exhaustive run or an aborted state-limited run.

    ``generated_states`` includes one seed for each distinct allocated start
    and every valid child state placed on the frontier.  ``pruned_states`` is
    the number of neighbour extensions rejected for one of the three stated
    reasons; no score-based or heuristic pruning is performed.
    """

    complete: bool
    expanded_states: int
    generated_states: int
    candidate_states: int
    pruned_visited: int
    pruned_untraversable: int
    pruned_budget: int

    @property
    def pruned_states(self) -> int:
        return (
            self.pruned_visited
            + self.pruned_untraversable
            + self.pruned_budget
        )


class ExactSearchLimitExceeded(RuntimeError):
    """Raised instead of returning a bounded search as an exact result."""

    def __init__(
        self,
        max_expanded_states: int,
        diagnostics: ExactSearchDiagnostics,
    ) -> None:
        self.max_expanded_states = max_expanded_states
        self.diagnostics = diagnostics
        super().__init__(
            "Exact path enumeration exceeded its expanded-state limit "
            f"({max_expanded_states:,}); no exact result is available"
        )


@dataclass(frozen=True, slots=True)
class ExactSearchResult:
    """All positive-score candidates from a completed exhaustive search."""

    candidates: tuple[ExactPathCandidate, ...]
    diagnostics: ExactSearchDiagnostics

    @property
    def best(self) -> ExactPathCandidate | None:
        """Return the exact best candidate under score-first ranking."""

        return self.candidates[0] if self.candidates else None

    def top(self, limit: int) -> tuple[ExactPathCandidate, ...]:
        """Return an exact top-k slice after exhaustive enumeration."""

        if limit < 0:
            raise ValueError("limit must be non-negative")
        return self.candidates[:limit]


@dataclass(frozen=True, slots=True)
class _SearchState:
    current: str
    path: tuple[str, ...]
    visited: frozenset[str]
    cost: int
    score: float


class ExactPathSolver:
    """Exhaustively enumerate tractable single-path extension problems.

    Completion guarantees that every traversable simple path beginning at any
    allocated node and costing at most ``max_points`` was expanded.  As in the
    current optimiser, returned candidates have positive cost and positive
    score.  Positive strict prefixes are retained, and candidates are ranked
    by total score first, efficiency second, then path lexicographically for
    deterministic ties.

    This is an exact ground-truth tool, not a production search strategy.
    Runtime and memory are exponential in the worst case.
    """

    DEFAULT_MAX_EXPANDED_STATES = 100_000

    def __init__(self, pathfinder, path_evaluator) -> None:
        self.pathfinder = pathfinder
        self.path_evaluator = path_evaluator

    def solve(
        self,
        allocated: Iterable[str],
        desired_stats: DesiredStats,
        max_points: int = 10,
        *,
        max_expanded_states: int | None = DEFAULT_MAX_EXPANDED_STATES,
    ) -> ExactSearchResult:
        """Return exact ranked candidates, or raise when the hard cap aborts.

        Passing ``None`` disables the safety cap and should only be done for a
        graph whose tractability has already been established.
        """

        if max_points < 0:
            raise ValueError("max_points must be non-negative")
        if max_expanded_states is not None and max_expanded_states <= 0:
            raise ValueError("max_expanded_states must be positive or None")

        allocated_set = frozenset(allocated)
        starts = sorted(allocated_set)
        frontier = [
            _SearchState(
                current=node_id,
                path=(node_id,),
                visited=frozenset({node_id}),
                cost=0,
                score=0.0,
            )
            for node_id in reversed(starts)
        ]

        candidates: list[ExactPathCandidate] = []
        node_scores: dict[str, float] = {}
        expanded_states = 0
        generated_states = len(frontier)
        pruned_visited = 0
        pruned_untraversable = 0
        pruned_budget = 0

        def diagnostics(*, complete: bool) -> ExactSearchDiagnostics:
            return ExactSearchDiagnostics(
                complete=complete,
                expanded_states=expanded_states,
                generated_states=generated_states,
                candidate_states=len(candidates),
                pruned_visited=pruned_visited,
                pruned_untraversable=pruned_untraversable,
                pruned_budget=pruned_budget,
            )

        while frontier:
            if (
                max_expanded_states is not None
                and expanded_states >= max_expanded_states
            ):
                raise ExactSearchLimitExceeded(
                    max_expanded_states,
                    diagnostics(complete=False),
                )

            state = frontier.pop()
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

            # Reverse insertion makes the lexicographically smallest child the
            # next state popped from the LIFO frontier.
            neighbours = sorted(
                set(self.pathfinder.adj.get(state.current, ())),
                reverse=True,
            )
            for neighbour in neighbours:
                if neighbour in state.visited:
                    pruned_visited += 1
                    continue

                if not self._is_traversable(neighbour, allocated_set):
                    pruned_untraversable += 1
                    continue

                is_allocated = neighbour in allocated_set
                next_cost = state.cost + (0 if is_allocated else 1)
                if next_cost > max_points:
                    pruned_budget += 1
                    continue

                next_score = state.score
                if not is_allocated:
                    if neighbour not in node_scores:
                        node_scores[neighbour] = (
                            self.path_evaluator.node_scorer.score_node(
                                neighbour,
                                desired_stats,
                            )
                        )
                    next_score += node_scores[neighbour]

                frontier.append(
                    _SearchState(
                        current=neighbour,
                        path=(*state.path, neighbour),
                        visited=state.visited | {neighbour},
                        cost=next_cost,
                        score=next_score,
                    )
                )
                generated_states += 1

        candidates.sort(
            key=lambda candidate: (
                -candidate.score,
                -candidate.efficiency,
                candidate.path,
            )
        )
        return ExactSearchResult(
            candidates=tuple(candidates),
            diagnostics=diagnostics(complete=True),
        )

    def _is_traversable(
        self,
        node_id: str,
        allocated: frozenset[str],
    ) -> bool:
        """Use the public traversal contract, with legacy compatibility."""

        public_method = getattr(self.pathfinder, "is_traversable", None)
        if callable(public_method):
            return bool(public_method(node_id, allocated))

        private_method = getattr(self.pathfinder, "_is_traversable", None)
        if not callable(private_method):
            raise TypeError(
                "pathfinder must provide is_traversable(node_id, allocated)"
            )
        return bool(private_method(node_id, allocated))
