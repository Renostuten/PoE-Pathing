from collections import deque

class PathFinder:
    def __init__(self, adj):
        self.adj = adj

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
                if neighbor not in visited:
                    queue.append((neighbor, path + [neighbor]))

        return None

    def distance(self, src, dst):
        path = self.shortest_path(src, dst)
        return len(path) - 1 if path else None