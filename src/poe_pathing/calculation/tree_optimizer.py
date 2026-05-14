class TreeOptimizer:
    def __init__(self, pathfinder, path_evaluator):
        self.pathfinder = pathfinder
        self.path_evaluator = path_evaluator

    def recommend_paths(self, allocated, desired_stats: dict[tuple[str, str], float], max_points=10, limit=10):
        distance, previous = self.pathfinder.shortest_paths_from_allocated(allocated)

        candidates = []

        for target, cost in distance.items():
            if target in allocated:
                continue

            if cost > max_points:
                continue

            path = self.reconstruct_path(previous, target)

            score = self.path_evaluator.score_path(path, allocated, desired_stats)
            efficiency = self.path_evaluator.efficiency(path, allocated, desired_stats)

            if score <= 0:
                continue

            candidates.append({
                "target": target,
                "path": path,
                "cost": cost,
                "score": score,
                "efficiency": efficiency,
            })

        candidates.sort(key=lambda c: c["efficiency"], reverse=True)
        return candidates[:limit]
    
    def reconstruct_path(self, previous, target):
        path = []
        current = target

        while current is not None:
            path.append(current)
            current = previous[current]

        path.reverse()
        return path