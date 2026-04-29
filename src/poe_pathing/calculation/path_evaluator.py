class PathEvaluator:
    def __init__(self, node_scorer):
        self.node_scorer = node_scorer

    def score_path(self, path, allocated):
        total = 0

        for node_id in path:
            if node_id not in allocated:
                total += self.node_scorer.score_node(node_id)

        return total

    def path_cost(self, path, allocated):
        return sum(1 for node_id in path if node_id not in allocated)

    def efficiency(self, path, allocated):
        cost = self.path_cost(path, allocated)

        if cost == 0:
            return 0

        return self.score_path(path, allocated) / cost