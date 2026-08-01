from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.calculation.path_evaluator import PathEvaluator
from poe_pathing.calculation.tree_optimizer import TreeOptimizer
from poe_pathing.graph.pathfinder import PathFinder


@dataclass(frozen=True)
class SyntheticNode:
    id: str
    class_start_index: int | None = None
    stats: tuple[str, ...] = ()


class SyntheticNodeLookup:
    def __init__(self, node_ids: set[str], class_starts: set[str]) -> None:
        self.nodes = {
            node_id: SyntheticNode(
                id=node_id,
                class_start_index=(
                    sorted(class_starts).index(node_id)
                    if node_id in class_starts
                    else None
                ),
            )
            for node_id in node_ids
        }

    def get(self, node_id: str) -> SyntheticNode | None:
        return self.nodes.get(node_id)


class NullStatParser:
    def parse(self, raw_stat: str) -> None:
        return None


class FixedNodeScorer:
    def __init__(
        self,
        scores: dict[str, float],
        node_lookup: SyntheticNodeLookup,
    ) -> None:
        self.scores = scores
        self.node_lookup = node_lookup
        self.stat_parser = NullStatParser()

    def score_node(
        self,
        node_id: str,
        desired_stats: dict[tuple[str, str], float],
    ) -> float:
        return self.scores.get(node_id, 0.0)


def undirected_graph(
    *edges: tuple[str, str],
    sort_adjacency: bool = True,
) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for left, right in edges:
        adjacency.setdefault(left, []).append(right)
        adjacency.setdefault(right, []).append(left)

    if sort_adjacency:
        for neighbours in adjacency.values():
            neighbours.sort()
    return adjacency


def make_optimizer(
    adjacency: dict[str, list[str]],
    scores: dict[str, float],
    *,
    class_starts: set[str] | None = None,
) -> TreeOptimizer:
    class_starts = class_starts or {"S"}
    node_ids = set(adjacency) | set(scores) | class_starts
    for neighbours in adjacency.values():
        node_ids.update(neighbours)

    lookup = SyntheticNodeLookup(node_ids, class_starts)
    scorer = FixedNodeScorer(scores, lookup)
    evaluator = PathEvaluator(scorer)
    return TreeOptimizer(PathFinder(adjacency, lookup), evaluator)


class TreeOptimizerRegressionTests(unittest.TestCase):
    def test_capped_multiple_seed_order_can_depend_on_hash_seed(self) -> None:
        program = """
import json
from tests.test_tree_optimizer import make_optimizer, undirected_graph

optimizer = make_optimizer(
    undirected_graph(("S1", "A"), ("S2", "B")),
    {"A": 10.0, "B": 20.0},
    class_starts=set(),
)
optimizer.MAX_EXPANDED_STATES = 2
result = optimizer.recommend_paths({"S1", "S2"}, {}, 1)
print(json.dumps(result, sort_keys=True))
"""
        outputs = []
        for hash_seed in ("1", "3", "8"):
            environment = os.environ.copy()
            environment["PYTHONHASHSEED"] = hash_seed
            completed = subprocess.run(
                [sys.executable, "-c", program],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            outputs.append(json.loads(completed.stdout))

        self.assertNotEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])

    def test_equal_score_extension_does_not_replace_more_efficient_prefix(
        self,
    ) -> None:
        optimizer = make_optimizer(
            undirected_graph(("S", "A"), ("A", "B")),
            {"A": 10.0},
        )

        raw_candidates = optimizer.find_candidate_paths({"S"}, {}, 2)
        recommendations = optimizer.recommend_paths({"S"}, {}, 2)

        self.assertEqual(raw_candidates[0]["path"], ["S", "A"])
        self.assertEqual(raw_candidates[0]["efficiency"], 10.0)
        self.assertEqual(recommendations[0]["path"], ["S", "A"])
        self.assertEqual(recommendations[0]["cost"], 1)
        self.assertEqual(recommendations[0]["efficiency"], 10.0)

    def test_negative_score_extension_does_not_replace_better_prefix(
        self,
    ) -> None:
        optimizer = make_optimizer(
            undirected_graph(("S", "A"), ("A", "B")),
            {"A": 10.0, "B": -9.0},
        )

        recommendations = optimizer.recommend_paths({"S"}, {}, 2)

        self.assertEqual(recommendations[0]["path"], ["S", "A"])
        self.assertEqual(recommendations[0]["score"], 10.0)
        self.assertEqual(recommendations[0]["cost"], 1)

    def test_bucket_width_three_can_lose_a_useful_visited_set(self) -> None:
        optimizer = make_optimizer(
            undirected_graph(
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
            ),
            {
                "X": 100.0,
                "R": 100.0,
                "Z1": 1.0,
                "Z2": 1.0,
                "Z3": 1.0,
            },
        )

        optimizer.STATES_PER_BUCKET = 3
        narrow = optimizer.recommend_paths({"S"}, {}, 5)
        optimizer.STATES_PER_BUCKET = 4
        wider = optimizer.recommend_paths({"S"}, {}, 5)

        self.assertEqual(narrow[0]["score"], 101.0)
        self.assertEqual(wider[0]["score"], 200.0)
        self.assertEqual(
            wider[0]["path"],
            ["S", "A0", "B0", "X", "Y", "R"],
        )

    def test_lifo_cap_depends_on_adjacency_order(self) -> None:
        first_low = make_optimizer(
            {
                "S": ["A", "Z"],
                "A": ["S"],
                "Z": ["S"],
            },
            {"A": 10.0},
        )
        first_high = make_optimizer(
            {
                "S": ["Z", "A"],
                "A": ["S"],
                "Z": ["S"],
            },
            {"A": 10.0},
        )
        first_low.MAX_EXPANDED_STATES = 2
        first_high.MAX_EXPANDED_STATES = 2

        self.assertEqual(first_low.recommend_paths({"S"}, {}, 1), [])
        self.assertEqual(
            first_high.recommend_paths({"S"}, {}, 1)[0]["path"],
            ["S", "A"],
        )

    def test_unallocated_class_start_is_not_traversed(self) -> None:
        optimizer = make_optimizer(
            undirected_graph(
                ("S", "A"),
                ("A", "T"),
                ("T", "B"),
            ),
            {"A": 1.0, "B": 100.0},
            class_starts={"S", "T"},
        )

        recommendations = optimizer.recommend_paths({"S"}, {}, 3)

        self.assertTrue(recommendations)
        self.assertTrue(
            all("T" not in recommendation["path"] for recommendation in recommendations)
        )
        self.assertTrue(
            all("B" not in recommendation["path"] for recommendation in recommendations)
        )


if __name__ == "__main__":
    unittest.main()
