from calculation.stat_parser import ParsedStat

class StatScorer:
    def __init__(self, desired_stats: dict[tuple[str, str], float]):
        self.desired_stats = desired_stats

    def score_node(self, parsed_stats: list[ParsedStat]) -> float:
        total = 0

        for stat in parsed_stats:
            key = (stat.stat_type, stat.modifier_type)
            weight = self.desired_stats.get(key, 0)
            total += stat.value * weight

        return total