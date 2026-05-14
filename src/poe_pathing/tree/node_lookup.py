import json
from tokenize import group

from graph.node_position import get_node_position
from poe_pathing.tree.passive_node import PassiveNode

class NodeLookup:
    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as f:
            self.tree = json.load(f)

        self.lookup = {}

        for node_id, node in self.tree["nodes"].items():
            group_id = node.get("group")

            if group_id is None:
                continue

            if node.get("isMastery", False):
                continue

            if str(group_id) not in self.tree["groups"]:
                print(f"Skipping node {node_id}: group {group_id} not found")
                continue
            x, y = get_node_position(self.tree, node_id)

            self.lookup[node_id] = {
                "id": node_id,
                "classStartIndex": node.get("classStartIndex"),
                "name": node["name"],
                "x": x,
                "y": y,
                "isNotable": node.get("isNotable", False),
                "isKeystone": node.get("isKeystone", False),
                "stats": node.get("stats", [])
            }

    def get(self, node_id) -> PassiveNode:
        node_data = self.lookup.get(node_id)
        if not node_data:
            raise ValueError(f"Node not found: {node_id}")
        return PassiveNode(
            id=node_data["id"],
            name=node_data["name"],
            is_keystone=node_data["isKeystone"],
            is_notable=node_data["isNotable"],
            stats=node_data["stats"]
        )