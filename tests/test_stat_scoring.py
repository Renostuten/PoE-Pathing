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
from poe_pathing.tree.passive_node import PassiveNode


class StaticNodeLookup:
    def __init__(self, nodes: dict[str, PassiveNode]) -> None:
        self.nodes = nodes

    def get(self, node_id: str) -> PassiveNode:
        return self.nodes[node_id]


def passive_node(node_id: str, stats: list[str]) -> PassiveNode:
    return PassiveNode(
        id=node_id,
        name=node_id,
        class_start_index=None,
        is_keystone=False,
        is_notable=False,
        stats=stats,
    )


class StatParsingAndScoringTests(unittest.TestCase):
    def test_parser_recognises_current_supported_modifier_forms(self) -> None:
        parser = StatParser()

        flat = parser.parse("+25 to maximum Life")
        increased = parser.parse("10% increased maximum Life")
        reduced = parser.parse("5% reduced Attack Speed")

        self.assertEqual(
            (flat.stat_type, flat.modifier_type, flat.value),
            ("maximum_life", "flat", 25.0),
        )
        self.assertEqual(
            (
                increased.stat_type,
                increased.modifier_type,
                increased.value,
            ),
            ("maximum_life", "increased_percent", 10.0),
        )
        self.assertEqual(
            (reduced.stat_type, reduced.modifier_type, reduced.value),
            ("attack_speed", "reduced_percent", 5.0),
        )
        self.assertIsNone(parser.parse("10% increased Armour"))

    def test_node_and_path_scores_use_linear_request_weights(self) -> None:
        nodes = {
            "A": passive_node(
                "A",
                [
                    "+10 to Strength",
                    "+5 to Dexterity",
                    "8% increased Physical Damage",
                ],
            ),
            "B": passive_node("B", ["+3 to Strength"]),
        }
        scorer = StatScorer(StatParser(), StaticNodeLookup(nodes))
        evaluator = PathEvaluator(scorer)
        desired_stats = {
            ("strength", "flat"): 2.0,
            ("dexterity", "flat"): -1.0,
            ("physical_damage", "increased_percent"): 0.5,
        }

        self.assertEqual(scorer.score_node("A", desired_stats), 19.0)
        self.assertEqual(
            evaluator.score_path(["A", "B"], {"A"}, desired_stats),
            6.0,
        )
        self.assertEqual(evaluator.path_cost(["A", "B"], {"A"}), 1)
        self.assertEqual(
            evaluator.efficiency(["A", "B"], {"A"}, desired_stats),
            6.0,
        )


if __name__ == "__main__":
    unittest.main()
