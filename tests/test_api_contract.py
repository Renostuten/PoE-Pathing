from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.app import RecommendRequest, recommend_paths


class RecommendationApiContractTests(unittest.TestCase):
    def test_desired_stat_keys_use_the_frontend_json_tuple_encoding(self) -> None:
        request = RecommendRequest(
            allocated=["58833"],
            desired_stats={
                json.dumps(["maximum_life", "flat"]): 2.5,
                "not-json": 99.0,
            },
        )

        self.assertEqual(
            request.parsed_desired_stats(),
            {("maximum_life", "flat"): 2.5},
        )

    def test_route_response_shape_remains_frontend_compatible(self) -> None:
        request = RecommendRequest(
            allocated=["58833"],
            max_points=1,
            desired_stats={
                json.dumps(["maximum_life", "flat"]): 1.0,
            },
        )

        response = recommend_paths(request)

        self.assertEqual(set(response), {"recommendations"})
        self.assertTrue(response["recommendations"])
        recommendation = response["recommendations"][0]
        self.assertEqual(
            set(recommendation),
            {
                "target",
                "path",
                "cost",
                "score",
                "efficiency",
                "stats_gained",
            },
        )
        self.assertEqual(recommendation["target"], recommendation["path"][-1])
        self.assertEqual(recommendation["cost"], 1)
        self.assertEqual(
            set(recommendation["stats_gained"]),
            {"desired", "other"},
        )
        for category in ("desired", "other"):
            for stat in recommendation["stats_gained"][category]:
                self.assertEqual(
                    set(stat),
                    {
                        "stat_type",
                        "modifier_type",
                        "value",
                        "raw_stats",
                    },
                )


if __name__ == "__main__":
    unittest.main()
