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
from poe_pathing.research.exact_solver import ExactPathSolver
from poe_pathing.research.priority_search import (
    OptimisticPrioritySearch,
    PrioritySearchConfig,
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


def make_components(
    adjacency: dict[str, list[str]],
    scores: dict[str, float] | None = None,
    class_starts: set[str] | None = None,
) -> tuple[PathFinder, PathEvaluator]:
    scores = scores or {}
    class_starts = class_starts or set()
    node_ids = set(adjacency) | set(scores) | class_starts
    for neighbours in adjacency.values():
        node_ids.update(neighbours)

    lookup = SyntheticNodeLookup(node_ids, scores, class_starts)
    pathfinder = PathFinder(adjacency, lookup)
    scorer = StatScorer(StatParser(), lookup)
    return pathfinder, PathEvaluator(scorer)


def make_search(
    adjacency: dict[str, list[str]],
    *,
    scores: dict[str, float] | None = None,
    class_starts: set[str] | None = None,
    config: PrioritySearchConfig | None = None,
) -> OptimisticPrioritySearch:
    pathfinder, evaluator = make_components(
        adjacency,
        scores,
        class_starts,
    )
    return OptimisticPrioritySearch(pathfinder, evaluator, config)


class OptimisticPrioritySearchTests(unittest.TestCase):
    def test_returns_valid_paths_and_keeps_useful_prefixes(self) -> None:
        adjacency = undirected_graph(("S", "A"), ("A", "B"))
        search = make_search(
            adjacency,
            scores={"A": 4},
            class_starts={"S"},
            config=PrioritySearchConfig(
                states_per_bucket=4,
                max_expanded_states=100,
                candidate_pool_size=10,
            ),
        )

        result = search.search(
            {"S"},
            DESIRED_STRENGTH,
            max_points=2,
        )

        self.assertFalse(result.diagnostics.truncated)
        self.assertEqual(
            [candidate.path for candidate in result.candidates],
            [("S", "A"), ("S", "A", "B")],
        )
        self.assertEqual(
            [candidate.efficiency for candidate in result.candidates],
            [4.0, 2.0],
        )
        for candidate in result.candidates:
            self.assertLessEqual(candidate.cost, 2)
            self.assertEqual(candidate.cost, len(candidate.path) - 1)
            self.assertTrue(
                all(
                    right in adjacency[left]
                    for left, right in zip(
                        candidate.path,
                        candidate.path[1:],
                    )
                )
            )

    def test_bound_orders_promising_state_before_global_cap(self) -> None:
        search = make_search(
            {
                "S": ["A", "B"],
                "A": ["S"],
                "B": ["S"],
            },
            scores={"A": 1, "B": 5},
            class_starts={"S"},
            config=PrioritySearchConfig(
                states_per_bucket=2,
                max_expanded_states=2,
                candidate_pool_size=10,
            ),
        )

        result = search.search(
            {"S"},
            DESIRED_STRENGTH,
            max_points=1,
        )

        self.assertTrue(result.diagnostics.truncated)
        self.assertEqual(result.diagnostics.expanded_states, 2)
        self.assertEqual(result.diagnostics.generated_states, 3)
        self.assertEqual(result.best.path, ("S", "B"))
        self.assertEqual(result.best.score, 5.0)

    def test_honours_unallocated_class_start_traversal_rule(self) -> None:
        search = make_search(
            undirected_graph(
                ("S", "A"),
                ("A", "T"),
                ("T", "B"),
            ),
            scores={"A": 1, "B": 20},
            class_starts={"S", "T"},
        )

        blocked = search.search(
            {"S"},
            DESIRED_STRENGTH,
            max_points=3,
        )
        self.assertTrue(
            all("T" not in candidate.path for candidate in blocked.candidates)
        )
        self.assertTrue(
            all("B" not in candidate.path for candidate in blocked.candidates)
        )

        allowed = search.search(
            {"S", "T"},
            DESIRED_STRENGTH,
            max_points=2,
        )
        self.assertIn(
            ("T", "B"),
            {candidate.path for candidate in allowed.candidates},
        )

    def test_width_four_recovers_visited_set_trap_but_width_three_does_not(
        self,
    ) -> None:
        adjacency = undirected_graph(
            ("S", "A0"),
            ("A0", "B0"),
            ("B0", "X"),
            ("S", "Z1"),
            ("S", "Z2"),
            ("S", "Z3"),
            ("Z1", "Y"),
            ("Z2", "Y"),
            ("Z3", "Y"),
            ("Y", "X"),
            ("Y", "R"),
        )
        scores = {
            "X": 100,
            "R": 100,
            "Z1": 1,
            "Z2": 1,
            "Z3": 1,
        }
        pathfinder, evaluator = make_components(
            adjacency,
            scores,
            {"S"},
        )

        exact = ExactPathSolver(pathfinder, evaluator).solve(
            {"S"},
            DESIRED_STRENGTH,
            max_points=5,
        )
        narrow = OptimisticPrioritySearch(
            pathfinder,
            evaluator,
            PrioritySearchConfig(
                states_per_bucket=3,
                max_expanded_states=1_000,
                candidate_pool_size=100,
            ),
        ).search({"S"}, DESIRED_STRENGTH, max_points=5)
        wide = OptimisticPrioritySearch(
            pathfinder,
            evaluator,
            PrioritySearchConfig(
                states_per_bucket=4,
                max_expanded_states=1_000,
                candidate_pool_size=100,
            ),
        ).search({"S"}, DESIRED_STRENGTH, max_points=5)

        optimum = ("S", "A0", "B0", "X", "Y", "R")
        self.assertEqual(exact.best.path, optimum)
        self.assertEqual(exact.best.score, 200.0)
        self.assertEqual(narrow.best.score, 101.0)
        self.assertGreater(narrow.diagnostics.beam_pruned_states, 0)
        self.assertEqual(wide.best.path, optimum)
        self.assertEqual(wide.best.score, 200.0)
        self.assertFalse(wide.diagnostics.truncated)

    def test_candidate_pool_and_ties_are_deterministic(self) -> None:
        search = make_search(
            {
                "S": ["B", "A"],
                "A": ["S"],
                "B": ["S"],
            },
            scores={"A": 5, "B": 5},
            class_starts={"S"},
            config=PrioritySearchConfig(
                states_per_bucket=2,
                max_expanded_states=100,
                candidate_pool_size=1,
            ),
        )

        first = search.search({"S"}, DESIRED_STRENGTH, max_points=1)
        second = search.search({"S"}, DESIRED_STRENGTH, max_points=1)

        self.assertEqual(first, second)
        self.assertEqual(first.best.path, ("S", "A"))
        self.assertEqual(len(first.candidates), 1)
        self.assertEqual(first.diagnostics.candidate_states, 2)
        self.assertTrue(first.diagnostics.candidate_pool_truncated)

    def test_config_rejects_non_positive_limits(self) -> None:
        for keyword in (
            "states_per_bucket",
            "max_expanded_states",
            "candidate_pool_size",
        ):
            with self.subTest(keyword=keyword):
                values = {
                    "states_per_bucket": 1,
                    "max_expanded_states": 1,
                    "candidate_pool_size": 1,
                }
                values[keyword] = 0
                with self.assertRaises(ValueError):
                    PrioritySearchConfig(**values)


if __name__ == "__main__":
    unittest.main()
