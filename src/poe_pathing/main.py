from graph.build import load_adj
from tree.node_lookup import NodeLookup
from export.exporter import export_tree_graph


TREE_PATH = "data/raw/skilltree-export_3.28.0.json"
OUTPUT_PATH = "data/processed/tree-graph.json"
TAG = "3.28.0"


def main() -> None:
    adj = load_adj(TREE_PATH)
    node_lookup = NodeLookup(TREE_PATH)

    export_tree_graph(
        output_path=OUTPUT_PATH,
        node_lookup=node_lookup,
        adj=adj,
    )


if __name__ == "__main__":
    main()