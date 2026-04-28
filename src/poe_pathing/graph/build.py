from collections import defaultdict
import json

def load_adj(path: str) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Build adjacency list of the passive tree graph
    adj: dict[str, list[str]] = defaultdict(list)

    # v1: ignores nodes with no connections (mastery nodes)
    for node_id, node in data["nodes"].items():
        in_nodes = node.get("in", [])
        out_nodes = node.get("out", [])
        is_mastery = node.get("isMastery", False)

        if (not in_nodes and not out_nodes) or is_mastery:
            continue

        # Add edges in both directions (undirected graph)
        for out_node in out_nodes:
            adj[node_id].append(out_node)
        
        for in_node in in_nodes:
            adj[in_node].append(node_id)

    return adj