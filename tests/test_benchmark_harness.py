from __future__ import annotations

import unittest

from scripts.benchmark_pathing import (
    analyse_conditional_pruning,
    execute_scenario,
    load_configuration,
    protected_special_nodes,
    real_services,
    should_run_research_pruning,
    DEFAULT_SCENARIOS,
    LOCKED_PRUNING_ESTIMATED_WORK_THRESHOLD,
)


class BenchmarkHarnessTests(unittest.TestCase):
    def test_real_pruning_policy_protects_special_node_kinds(self) -> None:
        services = real_services()
        protected = protected_special_nodes(services)
        raw_nodes = services.pathfinder.node_lookup.tree["nodes"]

        class_starts = {
            node_id
            for node_id in services.adjacency
            if raw_nodes[node_id].get("classStartIndex") is not None
        }
        keystones = {
            node_id
            for node_id in services.adjacency
            if raw_nodes[node_id].get("isKeystone", False)
        }
        jewel_sockets = {
            node_id
            for node_id in services.adjacency
            if raw_nodes[node_id].get("isJewelSocket", False)
            or raw_nodes[node_id].get("expansionJewel")
        }

        self.assertTrue(class_starts)
        self.assertTrue(keystones)
        self.assertTrue(jewel_sockets)
        self.assertTrue(class_starts <= protected)
        self.assertTrue(keystones <= protected)
        self.assertTrue(jewel_sockets <= protected)

    def test_shared_branch_fixture_runs_all_required_variants(self) -> None:
        configuration = load_configuration(DEFAULT_SCENARIOS)
        scenario = next(
            item
            for item in configuration["synthetic"]
            if item["id"] == "pruning_branch_heavy"
        )

        result = execute_scenario(
            configuration,
            scenario,
            "synthetic",
            "quick",
            repeats=1,
            measure_memory=False,
        )
        runs = {run["strategy"]: run for run in result["runs"]}

        self.assertTrue({
            "exact",
            "exact_pruned",
            "current_w3_cap200000",
            "current_w3_cap200000_cached",
            "current_w3_cap200000_pruned",
            "current_w3_cap200000_cached_pruned",
        } <= set(runs))
        self.assertTrue(all(run["valid"] for run in runs.values()))
        experiment_runs = {
            name: run
            for name, run in runs.items()
            if run.get("cache_experiment_variant")
        }
        self.assertEqual(
            set(experiment_runs),
            {
                "current_w3_cap200000",
                "current_w3_cap200000_cached",
                "current_w3_cap200000_pruned",
                "current_w3_cap200000_cached_pruned",
            },
        )
        for run in experiment_runs.values():
            measurement = run["measurement"]
            self.assertIn("search_node_scoring_ms_median", measurement)
            self.assertIn(
                "pruning_and_graph_materialisation_ms_median",
                measurement,
            )
            self.assertIn("pruning_node_scoring_ms_median", measurement)
            self.assertIn(
                "pruning_and_materialisation_ms_median",
                measurement,
            )
            self.assertIn("total_node_scoring_ms_median", measurement)
            self.assertIn(
                "search_excluding_node_scoring_ms_median",
                measurement,
            )
            self.assertIn("search_ms_median", measurement)
            self.assertIn("total_wall_clock_ms_median", measurement)
            self.assertIn("generated_states", run["diagnostics"])
            self.assertTrue(run["result"]["response_fingerprint_sha256"])

        baseline = runs["current_w3_cap200000"]
        cached = runs["current_w3_cap200000_cached"]
        pruned = runs["current_w3_cap200000_pruned"]
        cached_pruned = runs["current_w3_cap200000_cached_pruned"]
        self.assertFalse(baseline["cache_enabled"])
        self.assertTrue(cached["cache_enabled"])
        self.assertTrue(cached["score_cache_enabled"])
        self.assertFalse(baseline["cache_applicable"])
        self.assertFalse(cached["cache_applicable"])
        self.assertEqual(baseline["result"], cached["result"])
        self.assertEqual(pruned["result"], cached_pruned["result"])
        self.assertEqual(baseline["diagnostics"], cached["diagnostics"])
        self.assertEqual(pruned["diagnostics"], cached_pruned["diagnostics"])
        self.assertTrue(
            result["cache_experiment"][
                "uncached_cached_payloads_identical"
            ]
        )
        self.assertTrue(
            result["pruning"][
                "cached_reduced_graph_matches_uncached"
            ]
        )
        self.assertTrue(
            runs["exact_pruned"]["comparison_to_unpruned"][
                "best_objective_unchanged"
            ]
        )
        self.assertGreater(
            runs["exact_pruned"]["comparison_to_unpruned"][
                "generated_states_reduction"
            ],
            0,
        )
        self.assertGreater(
            runs["current_w3_cap200000_pruned"][
                "comparison_to_unpruned"
            ]["generated_states_reduction"],
            0,
        )
        self.assertEqual(
            result["pruning"]["diagnostics"]["removed_node_count"],
            8,
        )

    def test_real_cache_variants_use_vectors_and_match_uncached(self) -> None:
        configuration = load_configuration(DEFAULT_SCENARIOS)
        scenario = next(
            item
            for item in configuration["real_tree"]
            if item["id"] == "real_scion_flat_life_b5"
        )
        result = execute_scenario(
            configuration,
            scenario,
            "real_tree",
            "quick",
            repeats=1,
            measure_memory=False,
        )
        runs = {run["strategy"]: run for run in result["runs"]}
        baseline = runs["current_w3_cap200000"]
        cached = runs["current_w3_cap200000_cached"]
        pruned = runs["current_w3_cap200000_pruned"]
        cached_pruned = runs["current_w3_cap200000_cached_pruned"]

        self.assertTrue(cached["score_cache_applicable"])
        self.assertEqual(cached["score_cache_mode"], "parsed_node_vectors")
        self.assertEqual(baseline["result"], cached["result"])
        self.assertEqual(pruned["result"], cached_pruned["result"])
        self.assertEqual(baseline["diagnostics"], cached["diagnostics"])
        self.assertEqual(pruned["diagnostics"], cached_pruned["diagnostics"])
        self.assertTrue(
            result["pruning"][
                "cached_reduced_graph_matches_uncached"
            ]
        )

    def test_research_pruning_gate_uses_estimated_work(self) -> None:
        threshold = LOCKED_PRUNING_ESTIMATED_WORK_THRESHOLD
        self.assertFalse(
            should_run_research_pruning(
                999,
                100,
                estimated_work_threshold=threshold,
            )
        )
        self.assertFalse(
            should_run_research_pruning(
                1_997,
                19,
                estimated_work_threshold=threshold,
            )
        )
        self.assertTrue(
            should_run_research_pruning(
                1_997,
                20,
                estimated_work_threshold=threshold,
            )
        )
        self.assertFalse(
            should_run_research_pruning(
                1_997,
                100,
                estimated_work_threshold=None,
            )
        )

    def test_gate_analysis_excludes_synthetic_and_honours_locked_threshold(
        self,
    ) -> None:
        def scenario(
            scenario_id: str,
            scenario_type: str,
            budget: int,
            baseline_ms: float,
            pruned_ms: float,
        ) -> dict:
            comparison = {
                "best_objective_unchanged": True,
                "identical_top_k_fingerprint": False,
                "generated_states_reduction": 10,
            }
            return {
                "id": scenario_id,
                "type": scenario_type,
                "max_points": budget,
                "pruning": {
                    "diagnostics": {"original_node_count": 1_997}
                },
                "runs": [
                    {
                        "strategy": "current_w3_cap200000_cached",
                        "valid": True,
                        "measurement": {
                            "total_wall_clock_ms_median": baseline_ms
                        },
                    },
                    {
                        "strategy": (
                            "current_w3_cap200000_cached_pruned"
                        ),
                        "valid": True,
                        "measurement": {
                            "total_wall_clock_ms_median": pruned_ms
                        },
                        "comparison_to_unpruned": comparison,
                    },
                ],
            }

        scenarios = [
            scenario("real_b20", "real_tree", 20, 100.0, 90.0),
            scenario("synthetic_control", "synthetic", 100, 1.0, 0.1),
        ]
        first = analyse_conditional_pruning(scenarios)
        second = analyse_conditional_pruning(scenarios)

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "locked_gate_passed")
        locked = next(
            item
            for item in first["evaluations"]
            if item["estimated_work_threshold"]
            == LOCKED_PRUNING_ESTIMATED_WORK_THRESHOLD
        )
        self.assertEqual(locked["selected_scenario_count"], 1)
        self.assertEqual(locked["selected_fingerprint_change_count"], 1)
        self.assertEqual(len(locked["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
