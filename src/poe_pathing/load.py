from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any


def load_poe_tree(tag: str, cache_dir: str = ".cache/poe_tree", force_refresh: bool = False) -> dict[str, Any]:
    """
    Load PoE passive tree JSON for a specific skilltree-export release tag.
    - Caches to disk after first download.
    - Reuses cached file unless force_refresh=True.
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    local_file = cache_path / f"skilltree-export_{tag}.json"
    url = f"https://raw.githubusercontent.com/grindinggear/skilltree-export/{tag}/data.json"

    if local_file.exists() and not force_refresh:
        with local_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    # Download + write atomically (avoid partial files if interrupted)
    with urllib.request.urlopen(url) as resp:
        tree = json.load(resp)

    tmp_file = local_file.with_suffix(local_file.suffix + ".tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2, sort_keys=True)

    tmp_file.replace(local_file)
    return tree


if __name__ == "__main__":
    TAG = "3.28.0"
    tree = load_poe_tree(TAG, cache_dir="data")

    nodes = tree["nodes"]
    print("nodes:", len(nodes))