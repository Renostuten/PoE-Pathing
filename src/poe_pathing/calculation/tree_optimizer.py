from collections import defaultdict


class TreeOptimizer:
    STATES_PER_BUCKET = 3
    CANDIDATE_POOL_SIZE = 350
    MAX_EXPANDED_STATES = 200_000

    def __init__(self, pathfinder, path_evaluator):
        self.pathfinder = pathfinder
        self.path_evaluator = path_evaluator

    def recommend_paths(self, allocated, desired_stats: dict[tuple[str, str], float], max_points=10, limit=10):
        candidates = self.find_candidate_paths(allocated, desired_stats, max_points)
        candidates = self.remove_dominated_prefixes(candidates)
        candidates.sort(key=lambda c: (c["score"], c["efficiency"]), reverse=True)
        return [
            self.with_stats_gained(candidate, allocated, desired_stats)
            for candidate in candidates[:limit]
        ]

    def find_candidate_paths(self, allocated, desired_stats: dict[tuple[str, str], float], max_points):
        allocated = set(allocated)
        buckets = defaultdict(list)
        candidates = []
        frontier = []
        node_scores = {}
        expanded_states = 0

        for node_id in allocated:
            state = {
                "current": node_id,
                "path": [node_id],
                "visited": frozenset({node_id}),
                "cost": 0,
                "score": 0.0,
            }
            buckets[(node_id, 0)].append(state)
            frontier.append(state)

        while frontier:
            state = frontier.pop()
            expanded_states += 1

            if expanded_states > self.MAX_EXPANDED_STATES:
                break

            if state["cost"] > 0 and state["score"] > 0:
                candidates.append(self.build_candidate(state))

            if state["cost"] >= max_points:
                continue

            for neighbour in self.pathfinder.adj.get(state["current"], []):
                if neighbour in state["visited"]:
                    continue

                if not self.pathfinder._is_traversable(neighbour, allocated):
                    continue

                is_allocated = neighbour in allocated
                next_cost = state["cost"] + (0 if is_allocated else 1)

                if next_cost > max_points:
                    continue

                next_score = state["score"]
                if not is_allocated:
                    if neighbour not in node_scores:
                        node_scores[neighbour] = self.path_evaluator.node_scorer.score_node(neighbour, desired_stats)
                    next_score += node_scores[neighbour]

                next_state = {
                    "current": neighbour,
                    "path": [*state["path"], neighbour],
                    "visited": frozenset({*state["visited"], neighbour}),
                    "cost": next_cost,
                    "score": next_score,
                }

                if self.keep_state(next_state, buckets):
                    frontier.append(next_state)

        candidates.sort(key=lambda c: (c["score"], c["efficiency"]), reverse=True)
        return candidates[:self.CANDIDATE_POOL_SIZE]

    def keep_state(self, state, buckets):
        key = (state["current"], state["cost"])
        bucket = buckets[key]

        for existing in bucket:
            if (
                existing["score"] >= state["score"]
                and existing["visited"].issubset(state["visited"])
            ):
                return False

        buckets[key] = [
            existing
            for existing in bucket
            if not (
                state["score"] >= existing["score"]
                and state["visited"].issubset(existing["visited"])
            )
        ]

        buckets[key].append(state)
        buckets[key].sort(key=lambda item: item["score"], reverse=True)

        if len(buckets[key]) > self.STATES_PER_BUCKET:
            del buckets[key][self.STATES_PER_BUCKET:]

        return state in buckets[key]

    def build_candidate(self, state):
        cost = state["cost"]
        score = state["score"]

        return {
            "target": state["current"],
            "path": state["path"],
            "cost": cost,
            "score": score,
            "efficiency": score / cost if cost > 0 else 0,
        }

    def with_stats_gained(self, candidate, allocated, desired_stats):
        return {
            **candidate,
            "stats_gained": self.path_evaluator.stats_gained(candidate["path"], allocated, desired_stats),
        }
    
    def reconstruct_path(self, previous, target):
        path = []
        current = target

        while current is not None:
            path.append(current)
            current = previous[current]

        path.reverse()
        return path

    def remove_dominated_prefixes(self, candidates):
        filtered = []

        for candidate in candidates:
            candidate_path = candidate["path"]
            is_dominated = any(
                self.is_strict_prefix(candidate_path, other["path"])
                and self._candidate_rank(other) > self._candidate_rank(candidate)
                for other in candidates
            )

            if not is_dominated:
                filtered.append(candidate)

        return filtered

    @staticmethod
    def _candidate_rank(candidate: dict) -> tuple[float, float]:
        return candidate["score"], candidate["efficiency"]

    def is_strict_prefix(self, path, other_path):
        return len(path) < len(other_path) and other_path[:len(path)] == path
