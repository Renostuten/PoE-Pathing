import json
from build import load_adj

adj = load_adj("../data/skilltree-export_3.28.0.json")

# bfs, returns the path from src to dst
def shortest_path(src, dst):
    visited = set()
    queue = [(src, [src])]
    
    while queue:
        current, path = queue.pop(0)
        if current == dst:
            return path
        
        if current in visited:
            continue
        visited.add(current)
        
        for neighbor in adj.get(current, []):
            if neighbor not in visited:
                queue.append((neighbor, path + [neighbor]))
    
    return None

# bfs to find distance of shortest path
def distance(src, dst):
    path = shortest_path(src, dst)
    return len(path) - 1 if path else None