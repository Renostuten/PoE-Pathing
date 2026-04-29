from collections import defaultdict
import json


def is_drawable_node(node: dict, groups: dict) -> bool:
    group_id = node.get("group")

    if group_id is None:
        return False

    group = groups.get(str(group_id))

    if group is None:
        return False

    if node.get("ascendancyName") is not None:
        return False

    if node.get("isAscendancyStart", False):
        return False

    if node.get("isMastery", False):
        return False
    
    if node.get("isProxy", False):
        return False
    
    if node.get("name") == "Medium Jewel Socket":
        return False

    return True


def load_adj(path: str) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = data["nodes"]
    groups = data["groups"]

    valid_node_ids = {
        node_id
        for node_id, node in nodes.items()
        if is_drawable_node(node, groups)
    }

    adj: dict[str, set[str]] = defaultdict(set)

    for node_id, node in nodes.items():
        if node_id not in valid_node_ids:
            continue

        neighbours = node.get("in", []) + node.get("out", [])

        for neighbour_id in neighbours:
            if neighbour_id not in valid_node_ids:
                continue

            adj[node_id].add(neighbour_id)
            adj[neighbour_id].add(node_id)

    return {
        node_id: sorted(neighbours)
        for node_id, neighbours in adj.items()
    }