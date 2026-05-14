from calculation.tree_optimizer import TreeOptimizer
from calculation.path_evaluator import PathEvaluator
from graph.pathfinder import PathFinder
from calculation.stat_scorer import StatScorer
from calculation.stat_parser import StatParser
from graph.build import load_adj
from tree.node_lookup import NodeLookup

PATH = "../data/raw/skilltree-export_3.28.0.json"

class Container:
    def __init__(self):
        # Load data once
        self.tree = load_adj(PATH)

        # Core components
        self.node_lookup = NodeLookup(PATH)
        self.pathfinder = PathFinder(self.tree, self.node_lookup)
        self.stat_parser = StatParser()
        self.stat_scorer = StatScorer(self.stat_parser, self.node_lookup)
        self.path_evaluator = PathEvaluator(self.stat_scorer)

        # High-level service
        self.tree_optimizer = TreeOptimizer(
            self.pathfinder,
            self.path_evaluator,
        )


# Singleton instance
container = Container()