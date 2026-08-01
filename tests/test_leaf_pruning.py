from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.calculation.path_evaluator import PathEvaluator
from poe_pathing.graph.pathfinder import PathFinder
from poe_pathing.research.exact_solver import ExactPathSolver
from poe_pathing.research.leaf_pruning import prune_non_useful_leaves


DESIRED_STATS: dict[tuple[str, str], float] = {}


@dataclass(frozen=True)
class SyntheticNode:
    id: str
    class_start_index: int | None = None


class SyntheticNodeLookup:
    def __init__(self, node_ids: set[str], class_starts: set[str]) -> None:
        ordered_starts = sorted(class_starts)
        self.nodes = {
            node_id: SyntheticNode(
                id=node_id,
                class_start_index=(
                    ordered_starts.index(node_id)
                    if node_id in class_starts
                    else None
                ),
            )
            for node_id in node_ids
        }

    def get(self, node_id: str) -> SyntheticNode | None:
        return self.nodes.get(node_id)


class FixedNodeScorer:
    def __init__(
        self,
        scores: Mapping[str, float],
        node_lookup: SyntheticNodeLookup,
    ) -> None:
        self.scores = scores
        self.node_lookup = node_lookup

    def score_node(
        self,
        node_id: str,
        desired_stats: Mapping[tuple[str, str], float],
    ) -> float:
        del desired_stats
        return self.scores.get(node_id, 0.0)


def undirected_graph(*edges: tuple[str, str]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for left, right in edges:
        adjacency.setdefault(left, []).append(right)
        adjacency.setdefault(right, []).append(left)
    return adjacency


def make_exact_components(
    adjacency: Mapping[str, tuple[str, ...] | list[str]],
    scores: Mapping[str, float],
    *,
    class_starts: set[str] | None = None,
) -> tuple[ExactPathSolver, PathEvaluator]:
    starts = class_starts or {"S"}
    node_ids = set(adjacency) | set(scores) | starts
    for neighbours in adjacency.values():
        node_ids.update(neighbours)

    lookup = SyntheticNodeLookup(node_ids, starts)
    evaluator = PathEvaluator(FixedNodeScorer(scores, lookup))
    return ExactPathSolver(PathFinder(adjacency, lookup), evaluator), evaluator


class NonUsefulLeafPruningTests(unittest.TestCase):
    def test_non_useful_leaf_is_safely_removed_with_diagnostics(self) -> None:
        result = prune_non_useful_leaves(
            undirected_graph(("S", "L")),
            {},
            {"S"},
        )

        self.assertEqual(dict(result.adjacency), {"S": ()})
        self.assertEqual(result.removed_order, ("L",))
        self.assertEqual(result.diagnostics.original_node_count, 2)
        self.assertEqual(result.diagnostics.original_edge_count, 1)
        self.assertEqual(result.diagnostics.remaining_node_count, 1)
        self.assertEqual(result.diagnostics.remaining_edge_count, 0)
        self.assertEqual(result.diagnostics.removed_edge_count, 1)
        self.assertEqual(result.diagnostics.initial_queue_size, 1)
        self.assertEqual(result.diagnostics.enqueued_node_count, 1)
        self.assertEqual(result.diagnostics.removed_node_percentage, 50.0)
        self.assertEqual(result.diagnostics.max_peel_round, 0)

    def test_repeated_removal_peels_branch_and_creates_new_leaves(self) -> None:
        adjacency = undirected_graph(
            ("S", "A"),
            ("A", "B"),
            ("B", "C"),
            ("S", "V"),
        )

        result = prune_non_useful_leaves(
            adjacency,
            {"V": 5.0},
            {"S"},
        )

        self.assertEqual(
            dict(result.adjacency),
            {"S": ("V",), "V": ("S",)},
        )
        self.assertEqual(result.removed_order, ("C", "B", "A"))
        self.assertEqual(
            tuple(item.peel_round for item in result.diagnostics.removals),
            (0, 1, 2),
        )
        self.assertEqual(result.diagnostics.max_peel_round, 2)

    def test_zero_score_connector_between_start_and_value_is_retained(
        self,
    ) -> None:
        adjacency = undirected_graph(("S", "C"), ("C", "V"))

        result = prune_non_useful_leaves(
            adjacency,
            {"V": 10.0},
            {"S"},
        )

        self.assertEqual(dict(result.adjacency), {
            "C": ("S", "V"),
            "S": ("C",),
            "V": ("C",),
        })
        self.assertEqual(result.removed_order, ())

    def test_positive_leaf_is_retained(self) -> None:
        result = prune_non_useful_leaves(
            undirected_graph(("S", "P")),
            {"P": 0.1},
            {"S"},
        )

        self.assertEqual(set(result.adjacency), {"P", "S"})
        self.assertEqual(result.removed_order, ())

    def test_allocated_leaf_is_retained(self) -> None:
        result = prune_non_useful_leaves(
            undirected_graph(("A", "P")),
            {"P": 1.0},
            {"A"},
        )

        self.assertEqual(set(result.adjacency), {"A", "P"})
        self.assertEqual(result.removed_order, ())

    def test_required_start_or_endpoint_is_retained(self) -> None:
        result = prune_non_useful_leaves(
            undirected_graph(("S", "R")),
            {},
            {"S"},
            required_nodes={"R"},
        )

        self.assertEqual(set(result.adjacency), {"R", "S"})
        self.assertEqual(result.removed_order, ())

    def test_pruning_preserves_exact_best_path_score_cost_and_validity(
        self,
    ) -> None:
        adjacency = undirected_graph(
            ("S", "A"),
            ("A", "B"),
            ("A", "N"),
            ("B", "Z"),
        )
        scores = {"B": 10.0, "N": -2.0}
        pruned = prune_non_useful_leaves(adjacency, scores, {"S"})
        baseline_solver, baseline_evaluator = make_exact_components(
            adjacency,
            scores,
        )
        pruned_solver, pruned_evaluator = make_exact_components(
            pruned.adjacency,
            scores,
        )

        baseline = baseline_solver.solve(
            {"S"}, DESIRED_STATS, max_points=3
        )
        reduced = pruned_solver.solve({"S"}, DESIRED_STATS, max_points=3)

        self.assertEqual(pruned.removed_order, ("N", "Z"))
        self.assertEqual(baseline.best, reduced.best)
        self.assertEqual(baseline.best.path, ("S", "A", "B"))
        self.assertEqual(baseline.best.score, 10.0)
        self.assertEqual(baseline.best.cost, 2)
        for candidate, evaluator, graph in (
            (baseline.best, baseline_evaluator, adjacency),
            (reduced.best, pruned_evaluator, pruned.adjacency),
        ):
            self.assertEqual(len(candidate.path), len(set(candidate.path)))
            self.assertTrue(all(
                right in graph[left]
                for left, right in zip(candidate.path, candidate.path[1:])
            ))
            self.assertEqual(
                candidate.score,
                evaluator.score_path(candidate.path, {"S"}, DESIRED_STATS),
            )
            self.assertEqual(
                candidate.cost,
                evaluator.path_cost(candidate.path, {"S"}),
            )

    def test_pruning_reduces_exact_generated_states_on_branch_heavy_graph(
        self,
    ) -> None:
        adjacency = undirected_graph(("S", "A"), ("A", "V"))
        for branch in range(12):
            head = f"Z{branch:02d}"
            tail = f"Z{branch:02d}T"
            adjacency.setdefault("S", []).append(head)
            adjacency[head] = ["S", tail]
            adjacency[tail] = [head]

        scores = {"V": 20.0}
        pruned = prune_non_useful_leaves(adjacency, scores, {"S"})
        baseline_solver, _ = make_exact_components(adjacency, scores)
        reduced_solver, _ = make_exact_components(pruned.adjacency, scores)

        baseline = baseline_solver.solve(
            {"S"}, DESIRED_STATS, max_points=2
        )
        reduced = reduced_solver.solve({"S"}, DESIRED_STATS, max_points=2)

        self.assertEqual(baseline.best, reduced.best)
        self.assertEqual(pruned.diagnostics.removed_node_count, 24)
        self.assertLess(
            reduced.diagnostics.generated_states,
            baseline.diagnostics.generated_states,
        )
        self.assertEqual(reduced.diagnostics.generated_states, 3)

    def test_all_useful_nodes_and_isolated_keys_are_retained_read_only(
        self,
    ) -> None:
        # A appears only as a neighbour; I is an explicit isolated node.
        adjacency = {"S": ["B", "A"], "B": ["S"], "I": []}
        result = prune_non_useful_leaves(
            adjacency,
            {"S": 1.0, "A": 1.0, "B": 1.0, "I": 1.0},
            {"S"},
        )

        self.assertEqual(list(result.adjacency), ["A", "B", "I", "S"])
        self.assertEqual(result.adjacency["S"], ("A", "B"))
        self.assertEqual(result.adjacency["A"], ("S",))
        self.assertEqual(result.adjacency["I"], ())
        self.assertEqual(result.removed_order, ())
        self.assertIsNone(result.diagnostics.max_peel_round)
        with self.assertRaises(TypeError):
            result.adjacency["S"] = ()
        with self.assertRaises(TypeError):
            result.adjacency["S"][0] = "B"

    def test_no_nodes_useful_peels_over_several_rounds(self) -> None:
        result = prune_non_useful_leaves(
            undirected_graph(("S", "A"), ("A", "B"), ("B", "C")),
            {},
            {"S"},
        )

        self.assertEqual(dict(result.adjacency), {"S": ()})
        self.assertEqual(result.removed_order, ("C", "B", "A"))
        self.assertEqual(result.diagnostics.max_peel_round, 2)

    def test_cycle_has_no_leaves_to_remove(self) -> None:
        adjacency = undirected_graph(
            ("A", "B"),
            ("B", "C"),
            ("C", "A"),
        )

        result = prune_non_useful_leaves(adjacency, {}, set())

        self.assertEqual(set(result.adjacency), {"A", "B", "C"})
        self.assertEqual(result.removed_order, ())

    def test_negative_leaf_is_removed(self) -> None:
        result = prune_non_useful_leaves(
            undirected_graph(("S", "N")),
            {"N": -100.0},
            {"S"},
        )

        self.assertEqual(dict(result.adjacency), {"S": ()})
        self.assertEqual(result.removed_order, ("N",))

    def test_special_removal_predicate_can_protect_node_type(self) -> None:
        adjacency = undirected_graph(("S", "K"), ("S", "L"))

        result = prune_non_useful_leaves(
            adjacency,
            {},
            {"S"},
            can_remove=lambda node_id: node_id != "K",
        )

        self.assertEqual(
            dict(result.adjacency),
            {"K": ("S",), "S": ("K",)},
        )
        self.assertEqual(result.removed_order, ("L",))

    def test_zero_leaf_extension_disappears_but_best_prefix_is_preserved(
        self,
    ) -> None:
        adjacency = undirected_graph(("S", "P"), ("P", "Z"))
        scores = {"P": 5.0}
        pruned = prune_non_useful_leaves(adjacency, scores, {"S"})
        baseline_solver, _ = make_exact_components(adjacency, scores)
        reduced_solver, _ = make_exact_components(pruned.adjacency, scores)

        baseline = baseline_solver.solve(
            {"S"}, DESIRED_STATS, max_points=2
        )
        reduced = reduced_solver.solve({"S"}, DESIRED_STATS, max_points=2)

        self.assertIn(
            ("S", "P", "Z"),
            {candidate.path for candidate in baseline.candidates},
        )
        self.assertNotIn(
            ("S", "P", "Z"),
            {candidate.path for candidate in reduced.candidates},
        )
        self.assertEqual(baseline.best, reduced.best)


if __name__ == "__main__":
    unittest.main()
