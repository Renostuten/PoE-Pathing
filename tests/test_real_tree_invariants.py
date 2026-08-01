from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.services.container import container


class RealTreeInvariantTests(unittest.TestCase):
    def test_production_graph_is_sorted_symmetric_and_loop_free(self) -> None:
        edge_count = 0
        for node_id, neighbours in container.tree.items():
            self.assertEqual(neighbours, sorted(neighbours))
            self.assertNotIn(node_id, neighbours)
            for neighbour in neighbours:
                self.assertIn(node_id, container.tree[neighbour])
                if node_id < neighbour:
                    edge_count += 1

        self.assertEqual(len(container.tree), 1_997)
        self.assertEqual(edge_count, 2_325)

    def test_all_class_starts_follow_the_traversal_rule(self) -> None:
        starts = sorted(
            node_id
            for node_id in container.tree
            if container.node_lookup.get(node_id).class_start_index is not None
        )

        self.assertEqual(len(starts), 7)
        for node_id in starts:
            self.assertFalse(
                container.pathfinder._is_traversable(node_id, allocated=set())
            )
            self.assertTrue(
                container.pathfinder._is_traversable(
                    node_id,
                    allocated={node_id},
                )
            )

    def test_recommendation_is_connected_within_budget_and_rescored(self) -> None:
        allocated = {"58833"}
        desired_stats = {("maximum_life", "flat"): 1.0}
        recommendation = container.tree_optimizer.recommend_paths(
            allocated,
            desired_stats,
            max_points=5,
            limit=1,
        )[0]
        path = recommendation["path"]

        self.assertEqual(path[0], "58833")
        self.assertEqual(path[-1], recommendation["target"])
        self.assertEqual(len(path), len(set(path)))
        self.assertTrue(
            all(
                right in container.tree[left]
                for left, right in zip(path, path[1:])
            )
        )
        self.assertLessEqual(recommendation["cost"], 5)
        self.assertEqual(
            recommendation["cost"],
            container.path_evaluator.path_cost(path, allocated),
        )
        self.assertEqual(
            recommendation["score"],
            container.path_evaluator.score_path(
                path,
                allocated,
                desired_stats,
            ),
        )
        self.assertEqual(
            recommendation["efficiency"],
            container.path_evaluator.efficiency(
                path,
                allocated,
                desired_stats,
            ),
        )


if __name__ == "__main__":
    unittest.main()
