from calculation.stat_parser import StatParser
from poe_pathing.tree.node_lookup import NodeLookup

class StatScorer:
    def __init__(self, stat_parser: StatParser, node_lookup: NodeLookup):
        self.stat_parser = stat_parser
        self.node_lookup = node_lookup

    def score_node(
        self,
        node_id: str,
        desired_stats: dict[tuple[str, str], float],
    ) -> float:
        node = self.node_lookup.get(node_id)
        total = 0.0

        for raw_stat in node.stats:
            parsed = self.stat_parser.parse(raw_stat)

            if parsed is None:
                continue

            key = (parsed.stat_type, parsed.modifier_type)
            weight = desired_stats.get(key, 0.0)
            total += parsed.value * weight

        return total