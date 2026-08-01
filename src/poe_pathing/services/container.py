from pathlib import Path

from ..calculation.tree_optimizer import TreeOptimizer
from ..calculation.path_evaluator import PathEvaluator
from ..graph.pathfinder import PathFinder
from ..calculation.cached_stat_scorer import CachedStatScorer
from ..calculation.stat_scorer import StatScorer
from ..calculation.stat_parser import StatParser
from ..graph.build import load_adj
from ..tree.node_lookup import NodeLookup

PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "skilltree-export_3.28.0.json"

class Container:
    def __init__(self):
        # Load data once
        self.tree = load_adj(PATH)

        # Core components
        self.node_lookup = NodeLookup(PATH)
        self.pathfinder = PathFinder(self.tree, self.node_lookup)
        self.stat_parser = StatParser()
        # Keep the uncached scorer as a compatibility oracle for diagnostics
        # and research comparisons. Production requests use the equivalent
        # eagerly materialised vectors built once with this singleton.
        self.uncached_stat_scorer = StatScorer(
            self.stat_parser,
            self.node_lookup,
        )
        self.cached_stat_scorer = CachedStatScorer(
            self.stat_parser,
            self.node_lookup,
        )
        # Preserve the existing public attribute while routing production
        # queries through the equivalent cached implementation.
        self.stat_scorer = self.cached_stat_scorer
        self.path_evaluator = PathEvaluator(self.stat_scorer)

        # High-level service
        self.tree_optimizer = TreeOptimizer(
            self.pathfinder,
            self.path_evaluator,
        )


# Singleton instance
container = Container()
