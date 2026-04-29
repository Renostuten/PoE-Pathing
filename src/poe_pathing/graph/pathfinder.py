from collections import deque

class PathFinder:
    def __init__(self, adj, node_lookup, start_root_id):
        self.adj = adj
        self.node_lookup = node_lookup
        self.start_root_id = start_root_id

    def shortest_path(self, src, dst):
        visited = set()
        queue = deque([(src, [src])])

        while queue:
            current, path = queue.popleft()

            if current == dst:
                return path

            if current in visited:
                continue

            visited.add(current)

            for neighbor in self.adj.get(current, []):
                if not self._is_traversable(neighbor):
                    continue

                if neighbor not in visited:
                    queue.append((neighbor, path + [neighbor]))

        return None

    def distance(self, src, dst):
        path = self.shortest_path(src, dst)
        return len(path) - 1 if path else None
    
    def _is_class_start(self, node: dict) -> bool:
        return node["classStartIndex"] is not None


    def _is_traversable(self, node_id: str) -> bool:
        node = self.node_lookup.get(node_id)

        if node is None:
            return False

        if self._is_class_start(node):
            return node_id == self.start_root_id

        return True


    def _is_allocatable(self, node_id: str) -> bool:
        node = self.node_lookup.get(node_id)

        if node is None:
            return False

        return not node.get("isClassStart", False)