import json
from pathlib import Path


def build_edges(adj: dict[str, list[str]], node_lookup) -> list[dict[str, str]]:
    edges = []
    seen = set()

    for node_id, neighbours in adj.items():
        if node_lookup.get(node_id) is None:
            continue

        for neighbour_id in neighbours:
            if node_lookup.get(neighbour_id) is None:
                continue

            edge_key = tuple(sorted((node_id, neighbour_id)))

            if edge_key in seen:
                continue

            seen.add(edge_key)
            edges.append({"from": node_id, "to": neighbour_id})

    return edges

def build_nodes(adj: dict[str, list[str]], node_lookup) -> list[dict]:
    node_ids = set(adj.keys())

    for neighbours in adj.values():
        node_ids.update(neighbours)

    nodes = []

    for node_id in node_ids:
        node_data = node_lookup.get(node_id)

        if node_data is not None:
            nodes.append(node_data)

    return nodes


def export_tree_graph(
    output_path: str,
    node_lookup,
    adj: dict[str, list[str]],
    highlighted_path: list[str] | None = None,
) -> None:
    highlighted_path = highlighted_path or []

    export_data = {
        "nodes": build_nodes(adj, node_lookup),
        "edges": build_edges(adj, node_lookup),
        "highlightedPath": highlighted_path,
    }

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2)