from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import random
import sys
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.calculation.cached_stat_scorer import CachedStatScorer
from poe_pathing.calculation.path_evaluator import PathEvaluator
from poe_pathing.calculation.stat_parser import StatParser
from poe_pathing.calculation.stat_scorer import StatScorer
from poe_pathing.calculation.tree_optimizer import TreeOptimizer
from poe_pathing.graph.build import is_drawable_node
from poe_pathing.services.container import container
from poe_pathing.tree.node_lookup import NodeLookup
from poe_pathing.tree.passive_node import PassiveNode


StatKey = tuple[str, str]


class StaticNodeLookup:
    def __init__(self, nodes: dict[str, PassiveNode]) -> None:
        self.nodes = nodes

    def get(self, node_id: str) -> PassiveNode:
        node = self.nodes.get(node_id)
        if node is None:
            raise ValueError(f"Node not found: {node_id}")
        return node


class CountingStatParser(StatParser):
    def __init__(self) -> None:
        self.parse_count = 0

    def parse(self, raw_stat: str):
        self.parse_count += 1
        return super().parse(raw_stat)


def passive_node(node_id: str, stats: list[str]) -> PassiveNode:
    return PassiveNode(
        id=node_id,
        name=node_id,
        class_start_index=None,
        is_keystone=False,
        is_notable=False,
        stats=stats,
    )


class CachedStatScorerEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        tree_paths = sorted(
            (SOURCE_ROOT / "poe_pathing" / "data" / "raw").glob("*.json")
        )
        if len(tree_paths) != 1:
            raise AssertionError(
                f"Expected exactly one passive-tree export, found {tree_paths}"
            )
        cls.tree_path = tree_paths[0]

        with cls.tree_path.open("r", encoding="utf-8") as stream:
            cls.tree_data = json.load(stream)
        cls.drawable_node_ids = sorted(
            node_id
            for node_id, node in cls.tree_data["nodes"].items()
            if is_drawable_node(node, cls.tree_data["groups"])
        )

        with redirect_stdout(io.StringIO()):
            cls.node_lookup = NodeLookup(cls.tree_path)
        cls.parser = StatParser()
        cls.uncached = StatScorer(cls.parser, cls.node_lookup)
        cls.cached = CachedStatScorer(
            cls.parser,
            cls.node_lookup,
            cls.drawable_node_ids,
        )

        with (
            REPOSITORY_ROOT / "benchmarks" / "pathing_scenarios.json"
        ).open("r", encoding="utf-8") as stream:
            scenario_configuration = json.load(stream)
        cls.representative_profiles = {
            profile_name: {
                (item["stat_type"], item["modifier_type"]): item["weight"]
                for item in items
            }
            for profile_name, items in scenario_configuration["profiles"].items()
        }

        cls.supported_keys = sorted(
            {
                (contribution.stat_type, contribution.modifier_type)
                for node_id in cls.drawable_node_ids
                for contribution in cls.cached.vector_for(node_id).contributions
            }
        )

    def test_cached_vectors_match_fresh_parser_output_for_every_drawable_node(
        self,
    ) -> None:
        parsed_line_count = 0

        for node_id in self.drawable_node_ids:
            node = self.node_lookup.get(node_id)
            expected = tuple(
                parsed
                for raw_stat in node.stats
                if (parsed := self.parser.parse(raw_stat)) is not None
            )
            actual = self.cached.parsed_stats_for_node(node_id)
            self.assertEqual(actual, expected, node_id)
            parsed_line_count += len(actual)

        self.assertEqual(len(self.drawable_node_ids), 2045)
        self.assertEqual(parsed_line_count, 1064)

    def assert_profile_equivalent(
        self,
        profile_name: str,
        desired_stats: dict[StatKey, float],
    ) -> None:
        for node_id in self.drawable_node_ids:
            expected = self.uncached.score_node(node_id, desired_stats)
            actual = self.cached.score_node(node_id, desired_stats)
            if actual != expected:
                self.fail(
                    f"Cached score differs for profile {profile_name!r}, "
                    f"node {node_id}: expected {expected!r}, got {actual!r}"
                )

    def test_all_drawable_nodes_match_for_representative_profiles(self) -> None:
        self.assertGreater(len(self.drawable_node_ids), 0)
        self.assertEqual(
            self.cached.cached_node_count,
            len(self.drawable_node_ids),
        )

        for profile_name, desired_stats in self.representative_profiles.items():
            self.assert_profile_equivalent(profile_name, desired_stats)

    def test_all_drawable_nodes_match_for_generated_weight_combinations(self) -> None:
        random_source = random.Random(20260801)
        generated_profiles: list[tuple[str, dict[StatKey, float]]] = [
            ("all_zero", {key: 0.0 for key in self.supported_keys}),
            ("all_positive", {key: 1.25 for key in self.supported_keys}),
            ("all_negative", {key: -0.75 for key in self.supported_keys}),
            (
                "alternating_signs",
                {
                    key: (-2.0, 0.0, 3.5)[index % 3]
                    for index, key in enumerate(self.supported_keys)
                },
            ),
        ]
        generated_weights = (-3.0, -1.25, 0.0, 0.125, 0.5, 2.75)
        for profile_index in range(8):
            generated_profiles.append(
                (
                    f"seeded_{profile_index}",
                    {
                        key: random_source.choice(generated_weights)
                        for key in self.supported_keys
                        if random_source.random() < 0.7
                    },
                )
            )

        observed_weights = {
            weight
            for _, profile in generated_profiles
            for weight in profile.values()
        }
        self.assertTrue(any(weight < 0.0 for weight in observed_weights))
        self.assertIn(0.0, observed_weights)
        self.assertTrue(any(weight > 0.0 for weight in observed_weights))

        for profile_name, desired_stats in generated_profiles:
            self.assert_profile_equivalent(profile_name, desired_stats)

    def test_raw_stat_lines_are_not_reparsed_after_materialisation(self) -> None:
        nodes = {
            "A": passive_node(
                "A",
                [
                    "+10 to Strength",
                    "8% increased Physical Damage",
                    "10% increased Armour",
                ],
            ),
            "B": passive_node("B", ["+5 to Dexterity"]),
        }
        parser = CountingStatParser()
        scorer = CachedStatScorer(
            parser,
            StaticNodeLookup(nodes),  # type: ignore[arg-type]
            nodes,
        )
        parse_count_after_materialisation = parser.parse_count
        self.assertEqual(parse_count_after_materialisation, 4)

        profiles = (
            {("strength", "flat"): 1.0},
            {("physical_damage", "increased_percent"): -2.0},
            {("dexterity", "flat"): 0.0},
        )
        for _ in range(3):
            for desired_stats in profiles:
                scorer.score_node("A", desired_stats)
                scorer.score_node("B", desired_stats)

            PathEvaluator(scorer).stats_gained(
                ["A", "B"],
                set(),
                profiles[0],
            )

        self.assertEqual(parser.parse_count, parse_count_after_materialisation)

    def test_unsupported_node_retains_float_zero_identity(self) -> None:
        nodes = {'A': passive_node('A', ['10% increased Armour'])}
        scorer = CachedStatScorer(StatParser(), StaticNodeLookup(nodes), nodes)

        score = scorer.score_node('A', {('strength', 'flat'): 2.0})

        self.assertEqual(score, 0.0)
        self.assertIs(type(score), float)

    def test_cached_vectors_preserve_order_duplicates_unknown_and_raw_text(
        self,
    ) -> None:
        raw_stats = [
            "+10 to Strength",
            "+5 to Strength",
            "7 Strength",
            "-3 to Strength",
            "12% increased Damage",
        ]
        nodes = {"A": passive_node("A", raw_stats)}
        scorer = CachedStatScorer(
            StatParser(),
            StaticNodeLookup(nodes),  # type: ignore[arg-type]
            nodes,
        )

        contributions = scorer.parsed_stats_for_node("A")
        self.assertEqual(
            [
                (
                    parsed.stat_type,
                    parsed.modifier_type,
                    parsed.value,
                    parsed.raw_text,
                )
                for parsed in contributions
            ],
            [
                ("strength", "flat", 10.0, "+10 to Strength"),
                ("strength", "flat", 5.0, "+5 to Strength"),
                ("strength", "unknown", 7.0, "7 Strength"),
                ("strength", "unknown", -3.0, "-3 to Strength"),
            ],
        )

    def test_stats_gained_matches_uncached_with_zero_and_negative_weights(
        self,
    ) -> None:
        nodes = {
            "A": passive_node(
                "A",
                [
                    "+10 to Strength",
                    "+5 to Strength",
                    "7 Strength",
                    "12% increased Damage",
                ],
            ),
            "B": passive_node("B", ["-3 to Strength", "+5 to Dexterity"]),
        }
        lookup = StaticNodeLookup(nodes)
        parser = StatParser()
        uncached = PathEvaluator(
            StatScorer(parser, lookup)  # type: ignore[arg-type]
        )
        cached = PathEvaluator(
            CachedStatScorer(  # type: ignore[arg-type]
                parser,
                lookup,
                nodes,
            )
        )
        desired_stats = {
            ("strength", "flat"): 0.0,
            ("strength", "unknown"): -2.0,
        }

        expected = uncached.stats_gained(
            ["A", "B"],
            set(),
            desired_stats,
        )
        actual = cached.stats_gained(
            ["A", "B"],
            set(),
            desired_stats,
        )

        self.assertEqual(actual, expected)
        self.assertEqual(
            [stat["stat_type"] for stat in actual["desired"]],
            ["strength", "strength"],
        )
        self.assertEqual(
            actual["desired"][0]["raw_stats"],
            ["+10 to Strength", "+5 to Strength"],
        )

    def test_public_vector_mapping_is_read_only(self) -> None:
        with self.assertRaises(TypeError):
            self.cached.node_vectors["new"] = self.cached.vector_for(  # type: ignore[index]
                self.drawable_node_ids[0]
            )

    def test_missing_node_raises_the_existing_error(self) -> None:
        desired_stats = {("strength", "flat"): 1.0}

        with self.assertRaisesRegex(ValueError, "Node not found: missing"):
            self.uncached.score_node("missing", desired_stats)
        with self.assertRaisesRegex(ValueError, "Node not found: missing"):
            self.cached.score_node("missing", desired_stats)


class CachedStatScorerContainerIntegrationTests(unittest.TestCase):
    def test_container_keeps_uncached_oracle_and_uses_one_cached_scorer(self) -> None:
        self.assertIs(type(container.uncached_stat_scorer), StatScorer)
        self.assertIsInstance(container.cached_stat_scorer, CachedStatScorer)
        self.assertIs(container.stat_scorer, container.cached_stat_scorer)
        self.assertIs(
            container.path_evaluator.node_scorer,
            container.cached_stat_scorer,
        )
        self.assertIs(
            container.tree_optimizer.path_evaluator,
            container.path_evaluator,
        )
        self.assertEqual(
            container.cached_stat_scorer.cached_node_count,
            len(container.node_lookup.lookup),
        )

    def test_cached_production_recommendations_equal_uncached_oracle(self) -> None:
        desired_stats = {
            ("maximum_life", "flat"): 1.0,
            ("fire_damage", "increased_percent"): -0.25,
        }
        uncached_optimizer = TreeOptimizer(
            container.pathfinder,
            PathEvaluator(container.uncached_stat_scorer),
        )

        expected = uncached_optimizer.recommend_paths(
            {"58833"}, desired_stats, max_points=2, limit=3
        )
        actual = container.tree_optimizer.recommend_paths(
            {"58833"}, desired_stats, max_points=2, limit=3
        )

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
