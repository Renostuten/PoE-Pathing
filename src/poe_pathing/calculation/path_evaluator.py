class PathEvaluator:
    def __init__(self, node_scorer):
        self.node_scorer = node_scorer

    def score_path(self, 
                   path,
                   allocated,
                   desired_stats: dict[tuple[str, str], float]):
        total = 0

        for node_id in path:
            if node_id not in allocated:
                total += self.node_scorer.score_node(node_id, desired_stats)

        return total

    def path_cost(self, path, allocated):
        return sum(1 for node_id in path if node_id not in allocated)

    def efficiency(self, path, allocated, desired_stats: dict[tuple[str, str], float]):
        cost = self.path_cost(path, allocated)

        if cost == 0:
            return 0

        return self.score_path(path, allocated, desired_stats) / cost

    def stats_gained(self, path, allocated, desired_stats: dict[tuple[str, str], float]):
        gained = {}

        for node_id in path:
            if node_id in allocated:
                continue

            node = self.node_scorer.node_lookup.get(node_id)

            for raw_stat in node.stats:
                parsed = self.node_scorer.stat_parser.parse(raw_stat)

                if parsed is None:
                    continue

                key = (parsed.stat_type, parsed.modifier_type)

                if key not in gained:
                    gained[key] = {
                        "stat_type": parsed.stat_type,
                        "modifier_type": parsed.modifier_type,
                        "value": 0.0,
                        "raw_stats": [],
                    }

                gained[key]["value"] += parsed.value
                gained[key]["raw_stats"].append(raw_stat)

        desired = []
        other = []

        for key, stat in gained.items():
            if key in desired_stats:
                desired.append(stat)
            else:
                other.append(stat)

        sort_key = lambda stat: (stat["stat_type"], stat["modifier_type"])

        return {
            "desired": sorted(desired, key=sort_key),
            "other": sorted(other, key=sort_key),
        }
