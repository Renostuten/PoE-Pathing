import json

from poe_pathing.graph.node_position import get_node_position

class NodeLookup:
    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as f:
            self.tree = json.load(f)

        self.lookup = {}

        for node_id, node in self.tree["nodes"].items():
            x, y = get_node_position(self.tree, node_id)

            self.lookup[node_id] = {
                "id": node_id,
                "name": node["name"],
                "x": x,
                "y": y,
                "stats": node.get("stats", [])
            }

    def get(self, node_id):
        return self.lookup.get(node_id)