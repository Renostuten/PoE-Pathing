from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.research.current_search_probe import CurrentSearchProbe
from poe_pathing.services.container import Container
from tests.test_tree_optimizer import make_optimizer, undirected_graph


class CurrentSearchProbeTests(unittest.TestCase):
    def test_matches_production_on_visited_set_bucket_adversary(self) -> None:
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
            "X": 100.0,
            "R": 100.0,
            "Z1": 1.0,
            "Z2": 1.0,
            "Z3": 1.0,
        }
        production = make_optimizer(adjacency, scores)
        probe = CurrentSearchProbe(
            production.pathfinder,
            production.path_evaluator,
        )
        production.STATES_PER_BUCKET = 3
        probe.STATES_PER_BUCKET = 3

        expected_candidates = production.find_candidate_paths(
            {"S"}, {}, 5
        )
        actual_candidates = probe.find_candidate_paths({"S"}, {}, 5)
        expected_recommendations = production.recommend_paths(
            {"S"}, {}, 5
        )
        result = probe.recommend_paths_with_diagnostics({"S"}, {}, 5)

        self.assertEqual(actual_candidates, expected_candidates)
        self.assertEqual(
            list(result.recommendations), expected_recommendations
        )
        self.assertEqual(result.recommendations[0]["score"], 101.0)
        self.assertGreater(
            result.diagnostics.bucket_width_pruned_states, 0
        )

    def test_matches_cap_quirk_and_multiple_seed_order(self) -> None:
        production = make_optimizer(
            undirected_graph(("S1", "A"), ("S2", "B")),
            {"A": 10.0, "B": 20.0},
            class_starts=set(),
        )
        probe = CurrentSearchProbe(
            production.pathfinder,
            production.path_evaluator,
        )
        production.MAX_EXPANDED_STATES = 2
        probe.MAX_EXPANDED_STATES = 2

        expected = production.recommend_paths({"S1", "S2"}, {}, 1)
        result = probe.recommend_paths_with_diagnostics(
            {"S1", "S2"}, {}, 1
        )
        diagnostics = result.diagnostics

        self.assertEqual(list(result.recommendations), expected)
        self.assertIn(
            result.recommendations[0]["path"],
            (["S1", "A"], ["S2", "B"]),
        )
        self.assertEqual(diagnostics.seeded_states, 2)
        self.assertEqual(diagnostics.generated_states, 3)
        self.assertEqual(diagnostics.expanded_states, 3)
        self.assertEqual(diagnostics.processed_states, 2)
        self.assertTrue(diagnostics.cap_truncated)
        self.assertEqual(diagnostics.cap_discarded_states, 1)

    def test_matches_candidate_pool_and_inherited_prefix_objective(self) -> None:
        production = make_optimizer(
            undirected_graph(("S", "A"), ("A", "B"), ("S", "C")),
            {"A": 10.0, "C": 9.0},
        )
        probe = CurrentSearchProbe(
            production.pathfinder,
            production.path_evaluator,
        )
        production.CANDIDATE_POOL_SIZE = 1
        probe.CANDIDATE_POOL_SIZE = 1

        expected = production.recommend_paths({"S"}, {}, 2)
        result = probe.recommend_paths_with_diagnostics({"S"}, {}, 2)

        self.assertEqual(list(result.recommendations), expected)
        self.assertEqual(result.recommendations[0]["path"], ["S", "A"])
        self.assertGreater(result.diagnostics.candidate_states, 1)
        self.assertEqual(result.diagnostics.returned_candidate_states, 1)
        self.assertTrue(result.diagnostics.candidate_pool_truncated)
        self.assertEqual(
            result.diagnostics.candidate_pool_discarded_states,
            result.diagnostics.candidate_states - 1,
        )

    def test_matches_production_on_small_real_tree_query(self) -> None:
        components = Container()
        production = components.tree_optimizer
        probe = CurrentSearchProbe(
            production.pathfinder,
            production.path_evaluator,
        )
        desired_stats = {("maximum_life", "flat"): 1.0}

        expected_candidates = production.find_candidate_paths(
            {"58833"}, desired_stats, 1
        )
        actual_candidates = probe.find_candidate_paths(
            {"58833"}, desired_stats, 1
        )
        expected_recommendations = production.recommend_paths(
            {"58833"}, desired_stats, 1
        )
        result = probe.recommend_paths_with_diagnostics(
            {"58833"}, desired_stats, 1
        )

        self.assertEqual(actual_candidates, expected_candidates)
        self.assertEqual(
            list(result.recommendations), expected_recommendations
        )
        self.assertFalse(result.diagnostics.cap_truncated)


if __name__ == "__main__":
    unittest.main()
