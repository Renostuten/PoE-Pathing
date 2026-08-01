from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.calculation.path_evaluator import PathEvaluator
from poe_pathing.calculation.stat_parser import StatParser
from poe_pathing.calculation.stat_scorer import StatScorer
from poe_pathing.graph.pathfinder import PathFinder
from poe_pathing.research.exact_solver import (
    ExactPathSolver,
    ExactSearchLimitExceeded,
)
from poe_pathing.tree.passive_node import PassiveNode


DESIRED_STRENGTH = {("strength", "flat"): 1.0}


class SyntheticNodeLookup:
    def __init__(
        self,
        node_ids: set[str],
        scores: dict[str, float],
        class_starts: set[str],
    ) -> None:
        self.nodes = {
            node_id: PassiveNode(
                id=node_id,
                name=node_id,
                class_start_index=(
                    sorted(class_starts).index(node_id)
                    if node_id in class_starts
                    else None
                ),
                is_keystone=False,
                is_notable=False,
                stats=(
                    [f"+{scores[node_id]:g} to Strength"]
                    if scores.get(node_id, 0.0) != 0
                    else []
                ),
            )
            for node_id in node_ids
        }

    def get(self, node_id: str) -> PassiveNode | None:
        return self.nodes.get(node_id)


def undirected_graph(*edges: tuple[str, str]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for left, right in edges:
        adjacency.setdefault(left, []).append(right)
        adjacency.setdefault(right, []).append(left)
    return adjacency


def make_solver(
    adjacency: dict[str, list[str]],
    scores: dict[str, float] | None = None,
    class_starts: set[str] | None = None,
) -> ExactPathSolver:
    scores = scores or {}
    class_starts = class_starts or set()
    node_ids = set(adjacency) | set(scores) | class_starts
    for neighbours in adjacency.values():
        node_ids.update(neighbours)

    lookup = SyntheticNodeLookup(node_ids, scores, class_starts)
    pathfinder = PathFinder(adjacency, lookup)
    scorer = StatScorer(StatParser(), lookup)
    evaluator = PathEvaluator(scorer)
    return ExactPathSolver(pathfinder, evaluator)


def candidates_by_path(result) -> dict[tuple[str, ...], object]:
    return {candidate.path: candidate for candidate in result.candidates}


class ExactPathSolverTests(unittest.TestCase):
    def test_reaches_value_behind_zero_score_connector(self) -> None:
        solver = make_solver(
            undirected_graph(("S", "A"), ("A", "B")),
            scores={"B": 12},
            class_starts={"S"},
        )

        result = solver.solve({"S"}, DESIRED_STRENGTH, max_points=2)

        self.assertTrue(result.diagnostics.complete)
        self.assertEqual(result.diagnostics.expanded_states, 3)
        self.assertEqual(result.diagnostics.generated_states, 3)
        self.assertEqual(result.diagnostics.pruned_visited, 2)
        self.assertEqual(result.diagnostics.pruned_states, 2)
        self.assertIsNotNone(result.best)
        self.assertEqual(result.best.path, ("S", "A", "B"))
        self.assertEqual(result.best.cost, 2)
        self.assertEqual(result.best.score, 12.0)
        self.assertEqual(result.best.efficiency, 6.0)

    def test_retains_a_positive_strict_prefix(self) -> None:
        solver = make_solver(
            undirected_graph(("S", "A"), ("A", "B")),
            scores={"A": 4},
            class_starts={"S"},
        )

        result = solver.solve({"S"}, DESIRED_STRENGTH, max_points=2)

        self.assertEqual(
            [candidate.path for candidate in result.candidates],
            [("S", "A"), ("S", "A", "B")],
        )
        self.assertEqual(
            [candidate.efficiency for candidate in result.candidates],
            [4.0, 2.0],
        )

    def test_ranks_total_score_before_efficiency(self) -> None:
        solver = make_solver(
            undirected_graph(
                ("S", "A"),
                ("A", "X"),
                ("S", "B"),
            ),
            scores={"X": 10, "B": 9},
            class_starts={"S"},
        )

        result = solver.solve({"S"}, DESIRED_STRENGTH, max_points=2)

        self.assertEqual(result.best.path, ("S", "A", "X"))
        self.assertEqual(result.best.score, 10.0)
        self.assertEqual(result.best.efficiency, 5.0)
        branch = candidates_by_path(result)[("S", "B")]
        self.assertEqual(branch.score, 9.0)
        self.assertEqual(branch.efficiency, 9.0)

    def test_enumerates_both_directions_around_a_cycle_without_repeats(
        self,
    ) -> None:
        solver = make_solver(
            undirected_graph(
                ("S", "A"),
                ("A", "B"),
                ("B", "S"),
            ),
            scores={"A": 1, "B": 2},
            class_starts={"S"},
        )

        result = solver.solve({"S"}, DESIRED_STRENGTH, max_points=2)
        paths = [candidate.path for candidate in result.candidates]

        self.assertEqual(
            paths,
            [
                ("S", "A", "B"),
                ("S", "B", "A"),
                ("S", "B"),
                ("S", "A"),
            ],
        )
        self.assertTrue(
            all(len(path) == len(set(path)) for path in paths),
        )

    def test_allocated_nodes_are_zero_cost_even_at_the_budget(self) -> None:
        solver = make_solver(
            undirected_graph(
                ("S", "C"),
                ("C", "A"),
                ("A", "B"),
            ),
            scores={"C": 5, "A": 100, "B": 9},
            class_starts={"S"},
        )

        result = solver.solve(
            {"S", "A"},
            DESIRED_STRENGTH,
            max_points=1,
        )
        paths = candidates_by_path(result)

        through_allocated = paths[("S", "C", "A")]
        self.assertEqual(through_allocated.cost, 1)
        self.assertEqual(through_allocated.score, 5.0)
        self.assertIn(("A", "C", "S"), paths)
        self.assertEqual(paths[("A", "B")].cost, 1)
        self.assertEqual(paths[("A", "B")].score, 9.0)

    def test_unallocated_class_start_is_blocked_but_allocated_one_is_allowed(
        self,
    ) -> None:
        solver = make_solver(
            undirected_graph(
                ("S", "A"),
                ("A", "T"),
                ("T", "B"),
            ),
            scores={"A": 1, "B": 20},
            class_starts={"S", "T"},
        )

        blocked = solver.solve({"S"}, DESIRED_STRENGTH, max_points=3)
        self.assertTrue(
            all("T" not in candidate.path for candidate in blocked.candidates)
        )
        self.assertTrue(
            all("B" not in candidate.path for candidate in blocked.candidates)
        )
        self.assertGreater(blocked.diagnostics.pruned_untraversable, 0)

        allowed = solver.solve(
            {"S", "T"},
            DESIRED_STRENGTH,
            max_points=2,
        )
        paths = candidates_by_path(allowed)
        self.assertIn(("T", "B"), paths)
        self.assertEqual(paths[("T", "B")].cost, 1)
        self.assertIn(("S", "A", "T", "B"), paths)
        self.assertEqual(paths[("S", "A", "T", "B")].cost, 2)

    def test_distinct_visited_sets_at_same_node_are_both_explored(self) -> None:
        solver = make_solver(
            undirected_graph(
                ("S", "A"),
                ("S", "B"),
                ("A", "X"),
                ("B", "X"),
                ("A", "H"),
            ),
            scores={"A": 5, "B": 1, "H": 20},
            class_starts={"S"},
        )

        result = solver.solve({"S"}, DESIRED_STRENGTH, max_points=4)
        paths = candidates_by_path(result)

        self.assertEqual(paths[("S", "A", "X")].score, 5.0)
        self.assertEqual(paths[("S", "B", "X")].score, 1.0)
        self.assertIn(("S", "B", "X", "A", "H"), paths)
        self.assertEqual(result.best.path, ("S", "B", "X", "A", "H"))
        self.assertEqual(result.best.score, 26.0)

    def test_equal_score_ties_are_lexical_and_duplicate_edges_are_ignored(
        self,
    ) -> None:
        adjacency = {
            "S": ["B", "A", "B"],
            "A": ["S"],
            "B": ["S"],
        }
        solver = make_solver(
            adjacency,
            scores={"A": 5, "B": 5},
            class_starts={"S"},
        )

        first = solver.solve({"S"}, DESIRED_STRENGTH, max_points=1)
        second = solver.solve({"S"}, DESIRED_STRENGTH, max_points=1)

        self.assertEqual(first, second)
        self.assertEqual(
            [candidate.path for candidate in first.top(2)],
            [("S", "A"), ("S", "B")],
        )
        self.assertEqual(first.diagnostics.generated_states, 3)

    def test_state_limit_raises_with_incomplete_diagnostics(self) -> None:
        solver = make_solver(
            undirected_graph(("S", "A")),
            scores={"A": 1},
            class_starts={"S"},
        )

        with self.assertRaises(ExactSearchLimitExceeded) as raised:
            solver.solve(
                {"S"},
                DESIRED_STRENGTH,
                max_points=1,
                max_expanded_states=1,
            )

        error = raised.exception
        self.assertEqual(error.max_expanded_states, 1)
        self.assertFalse(error.diagnostics.complete)
        self.assertEqual(error.diagnostics.expanded_states, 1)
        self.assertEqual(error.diagnostics.generated_states, 2)
        self.assertEqual(error.diagnostics.candidate_states, 0)

    def test_rejects_invalid_limits(self) -> None:
        solver = make_solver({}, class_starts={"S"})

        with self.assertRaises(ValueError):
            solver.solve({"S"}, DESIRED_STRENGTH, max_points=-1)
        with self.assertRaises(ValueError):
            solver.solve(
                {"S"},
                DESIRED_STRENGTH,
                max_expanded_states=0,
            )

        result = solver.solve({"S"}, DESIRED_STRENGTH, max_points=0)
        with self.assertRaises(ValueError):
            result.top(-1)


if __name__ == "__main__":
    unittest.main()
