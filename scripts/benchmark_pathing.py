#!/usr/bin/env python3
"""Benchmark path recommendation strategies against shared fixed scenarios.

Examples from the repository root:

    python scripts/benchmark_pathing.py --quick
    python scripts/benchmark_pathing.py --full --output docs/pathing-benchmark-results.json

Runtime and peak-memory fields are measurements and will vary by machine.
Scenario definitions, candidate fingerprints, scores, costs, gaps, validity,
and diagnostics are deterministic for a fixed interpreter and source tree.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
import gc
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter
import tracemalloc
from typing import Any, Callable, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
DEFAULT_SCENARIOS = REPOSITORY_ROOT / "benchmarks" / "pathing_scenarios.json"
DEFAULT_CANDIDATE_POOL_SIZE = 350
MIN_GRAPH_NODES_FOR_PRUNING = 1_000
# Prior full-matrix frontier: 1,997 retained graph nodes times budgets
# 5/10/15/20.  The locked value is evaluated, not recalibrated, in this run.
PRUNING_ESTIMATED_WORK_THRESHOLDS = (9_985, 19_970, 29_955, 39_940)
LOCKED_PRUNING_ESTIMATED_WORK_THRESHOLD = 39_940

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from poe_pathing.calculation.cached_stat_scorer import (  # noqa: E402
    CachedStatScorer,
)
from poe_pathing.calculation.path_evaluator import PathEvaluator  # noqa: E402
from poe_pathing.calculation.stat_scorer import StatScorer  # noqa: E402
from poe_pathing.calculation.tree_optimizer import TreeOptimizer  # noqa: E402
from poe_pathing.graph.pathfinder import PathFinder  # noqa: E402
from poe_pathing.research import (  # noqa: E402
    CurrentSearchProbe,
    ExactPathSolver,
    ExactSearchLimitExceeded,
    OptimisticPrioritySearch,
    PrioritySearchConfig,
    prune_non_useful_leaves,
)

with redirect_stdout(io.StringIO()):
    from poe_pathing.services.container import container  # noqa: E402


DesiredStats = dict[tuple[str, str], float]
Candidate = dict[str, Any]


@dataclass(frozen=True)
class SyntheticNode:
    id: str
    name: str
    class_start_index: int | None
    is_keystone: bool = False
    is_notable: bool = False
    stats: tuple[str, ...] = ()


class SyntheticNodeLookup:
    def __init__(
        self,
        node_ids: Iterable[str],
        class_starts: Iterable[str],
    ) -> None:
        starts = {node_id: index for index, node_id in enumerate(sorted(class_starts))}
        self.nodes = {
            node_id: SyntheticNode(
                id=node_id,
                name=node_id,
                class_start_index=starts.get(node_id),
            )
            for node_id in node_ids
        }

    def get(self, node_id: str) -> SyntheticNode | None:
        return self.nodes.get(node_id)


class NullStatParser:
    def parse(self, raw_stat: str) -> None:
        return None


class FixedNodeScorer:
    def __init__(
        self,
        scores: Mapping[str, float],
        node_lookup: SyntheticNodeLookup,
    ) -> None:
        self.scores = dict(scores)
        self.node_lookup = node_lookup
        self.stat_parser = NullStatParser()

    def score_node(
        self,
        node_id: str,
        desired_stats: Mapping[tuple[str, str], float],
    ) -> float:
        return self.scores.get(node_id, 0.0)


class TimedNodeScorer:
    """Benchmark-only proxy measuring time inside the selected scorer.

    Profiling runs are kept outside the search wall-clock samples because the
    two timer reads per call would otherwise distort the cached variant most.
    Attribute fallback preserves optional capabilities such as cached parsed
    stats used by ``PathEvaluator.stats_gained``.
    """

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.elapsed_ms = 0.0
        self.call_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def reset(self) -> None:
        self.elapsed_ms = 0.0
        self.call_count = 0

    def score_node(
        self,
        node_id: str,
        desired_stats: Mapping[tuple[str, str], float],
    ) -> float:
        started = perf_counter()
        try:
            return self.delegate.score_node(node_id, desired_stats)
        finally:
            self.elapsed_ms += (perf_counter() - started) * 1_000
            self.call_count += 1


class LegacyPrefixTreeOptimizer(TreeOptimizer):
    """The pre-investigation unconditional strict-prefix postprocessing."""

    def remove_dominated_prefixes(self, candidates):
        return [
            candidate
            for candidate in candidates
            if not any(
                self.is_strict_prefix(candidate["path"], other["path"])
                for other in candidates
            )
        ]


@dataclass(frozen=True)
class ScenarioServices:
    adjacency: Mapping[str, Sequence[str]]
    pathfinder: PathFinder
    evaluator: PathEvaluator


@dataclass(frozen=True)
class PrunedScenario:
    services: ScenarioServices
    diagnostics: dict[str, Any]
    measurement: dict[str, Any]


@dataclass(frozen=True)
class StrategySpec:
    label: str
    kind: str
    states_per_bucket: int | None = None
    max_expanded_states: int | None = None
    candidate_pool_size: int = DEFAULT_CANDIDATE_POOL_SIZE
    legacy_prefixes: bool = False


_REAL_UNCACHED_SERVICES: ScenarioServices | None = None
_REAL_CACHED_SERVICES: ScenarioServices | None = None
_REAL_CACHE_BUILD: dict[str, Any] | None = None


def load_configuration(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        configuration = json.load(handle)
    if configuration.get("schema_version") != 1:
        raise ValueError("Unsupported benchmark scenario schema")
    return configuration


def desired_stats_for(
    configuration: Mapping[str, Any],
    profile_name: str,
) -> DesiredStats:
    return {
        (item["stat_type"], item["modifier_type"]): float(item["weight"])
        for item in configuration["profiles"][profile_name]
    }


def synthetic_services(scenario: Mapping[str, Any]) -> ScenarioServices:
    adjacency = {
        str(node_id): [str(neighbour) for neighbour in neighbours]
        for node_id, neighbours in scenario["adjacency"].items()
    }
    node_ids = set(adjacency)
    for neighbours in adjacency.values():
        node_ids.update(neighbours)
    node_ids.update(str(node_id) for node_id in scenario.get("scores", {}))
    node_ids.update(str(node_id) for node_id in scenario.get("class_starts", ()))

    lookup = SyntheticNodeLookup(node_ids, scenario.get("class_starts", ()))
    pathfinder = PathFinder(adjacency, lookup)
    evaluator = PathEvaluator(
        FixedNodeScorer(
            {
                str(node_id): float(score)
                for node_id, score in scenario.get("scores", {}).items()
            },
            lookup,
        )
    )
    return ScenarioServices(adjacency, pathfinder, evaluator)


def real_services() -> ScenarioServices:
    """Return an explicitly uncached baseline, independent of the container default."""

    global _REAL_UNCACHED_SERVICES
    if _REAL_UNCACHED_SERVICES is None:
        _REAL_UNCACHED_SERVICES = ScenarioServices(
            adjacency=container.tree,
            pathfinder=container.pathfinder,
            evaluator=PathEvaluator(
                StatScorer(container.stat_parser, container.node_lookup)
            ),
        )
    return _REAL_UNCACHED_SERVICES


def real_cached_services() -> ScenarioServices:
    """Return one eagerly built cache, matching a long-lived app container."""

    global _REAL_CACHED_SERVICES, _REAL_CACHE_BUILD
    if _REAL_CACHED_SERVICES is None:
        started = perf_counter()
        scorer = CachedStatScorer(container.stat_parser, container.node_lookup)
        build_ms = (perf_counter() - started) * 1_000
        _REAL_CACHED_SERVICES = ScenarioServices(
            adjacency=container.tree,
            pathfinder=container.pathfinder,
            evaluator=PathEvaluator(scorer),
        )
        _REAL_CACHE_BUILD = {
            "build_ms": round(build_ms, 6),
            "cached_node_count": scorer.cached_node_count,
            "parsed_contribution_count": sum(
                len(vector.contributions)
                for vector in scorer.node_vectors.values()
            ),
            "timing_policy": (
                "one-time eager startup cost; excluded from steady-state "
                "per-query totals"
            ),
        }
    return _REAL_CACHED_SERVICES


def real_cache_build_measurement() -> dict[str, Any] | None:
    return dict(_REAL_CACHE_BUILD) if _REAL_CACHE_BUILD is not None else None


def instrument_services(
    services: ScenarioServices,
) -> tuple[ScenarioServices, TimedNodeScorer]:
    timer = TimedNodeScorer(services.evaluator.node_scorer)
    return (
        ScenarioServices(
            adjacency=services.adjacency,
            pathfinder=services.pathfinder,
            evaluator=PathEvaluator(timer),
        ),
        timer,
    )


def graph_node_ids(adjacency: Mapping[str, Sequence[str]]) -> set[str]:
    node_ids = set(adjacency)
    for neighbours in adjacency.values():
        node_ids.update(str(node_id) for node_id in neighbours)
    return node_ids


def should_run_research_pruning(
    graph_node_count: int,
    max_points: int,
    *,
    estimated_work_threshold: int | None = None,
) -> bool:
    """Cheap deterministic research gate; ``None`` keeps pruning disabled."""

    return (
        estimated_work_threshold is not None
        and graph_node_count >= MIN_GRAPH_NODES_FOR_PRUNING
        and graph_node_count * max_points >= estimated_work_threshold
    )


def protected_special_nodes(services: ScenarioServices) -> set[str]:
    """Conservatively protect nodes with production traversal semantics.

    The production graph builder has already removed masteries, proxies,
    ascendancy nodes, and the special medium jewel socket.  ``PathFinder``
    treats class starts specially.  The experiment additionally protects
    keystones, jewel sockets, expansion anchors, multiple-choice nodes, and any
    defensively encountered excluded kind because their semantic value is not
    represented by the current additive stat score.  Unknown lookup entries
    are protected rather than assuming they are ordinary leaves.
    """

    protected: set[str] = set()
    raw_tree = getattr(services.pathfinder.node_lookup, "tree", {})
    raw_nodes = raw_tree.get("nodes", {}) if isinstance(raw_tree, dict) else {}
    for node_id in sorted(graph_node_ids(services.adjacency)):
        try:
            node = services.pathfinder.node_lookup.get(node_id)
        except (KeyError, ValueError):
            protected.add(node_id)
            continue
        raw_node = raw_nodes.get(node_id, {})
        has_special_raw_semantics = bool(
            raw_node.get("isJewelSocket", False)
            or raw_node.get("expansionJewel")
            or raw_node.get("isMastery", False)
            or raw_node.get("isProxy", False)
            or raw_node.get("isAscendancyStart", False)
            or raw_node.get("ascendancyName") is not None
            or raw_node.get("isMultipleChoice", False)
            or raw_node.get("isMultipleChoiceOption", False)
            or raw_node.get("name") == "Medium Jewel Socket"
        )
        if (
            node is None
            or getattr(node, "class_start_index", None) is not None
            or getattr(node, "is_keystone", False)
            or has_special_raw_semantics
        ):
            protected.add(node_id)
    return protected


def _prepare_pruned_once(
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
) -> tuple[PrunedScenario, float, float]:
    node_ids = sorted(graph_node_ids(services.adjacency))

    score_started = perf_counter()
    node_scores = {
        node_id: services.evaluator.node_scorer.score_node(
            node_id,
            desired_stats,
        )
        for node_id in node_ids
    }
    scoring_ms = (perf_counter() - score_started) * 1_000

    peel_started = perf_counter()
    required_nodes = protected_special_nodes(services)
    result = prune_non_useful_leaves(
        services.adjacency,
        node_scores,
        allocated,
        required_nodes=required_nodes,
    )

    # Preserve the baseline adjacency order among retained neighbours.  The
    # production bounded LIFO search is order-sensitive, so sorting only the
    # pruned variant would confound node removal with a traversal-order change.
    active_nodes = frozenset(result.adjacency)
    reduced_adjacency = {
        node_id: [
            neighbour
            for neighbour in services.adjacency.get(node_id, ())
            if neighbour in active_nodes
        ]
        for node_id in services.adjacency
        if node_id in active_nodes
    }
    for node_id in sorted(active_nodes - set(reduced_adjacency)):
        reduced_adjacency[node_id] = []

    reduced_services = ScenarioServices(
        adjacency=reduced_adjacency,
        pathfinder=PathFinder(
            reduced_adjacency,
            services.pathfinder.node_lookup,
        ),
        evaluator=services.evaluator,
    )
    peel_and_materialise_ms = (perf_counter() - peel_started) * 1_000

    raw_diagnostics = result.diagnostics
    diagnostics = {
        "original_node_count": raw_diagnostics.original_node_count,
        "original_edge_count": raw_diagnostics.original_edge_count,
        "remaining_node_count": raw_diagnostics.remaining_node_count,
        "remaining_edge_count": raw_diagnostics.remaining_edge_count,
        "removed_node_count": raw_diagnostics.removed_node_count,
        "removed_node_percentage": round(
            raw_diagnostics.removed_node_percentage,
            6,
        ),
        "removed_edge_count": raw_diagnostics.removed_edge_count,
        "initial_queue_size": raw_diagnostics.initial_queue_size,
        "enqueued_node_count": raw_diagnostics.enqueued_node_count,
        "max_peel_round": raw_diagnostics.max_peel_round,
        "protected_special_node_count": len(required_nodes),
        "protected_special_nodes": sorted(required_nodes),
        "adjacency_order_policy": (
            "baseline node and retained-neighbour order preserved"
        ),
        "score_policy": "production StatScorer score <= 0",
    }
    fingerprint_payload = {
        node_id: list(neighbours)
        for node_id, neighbours in reduced_adjacency.items()
    }
    diagnostics["reduced_graph_fingerprint_sha256"] = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return (
        PrunedScenario(reduced_services, diagnostics, {}),
        scoring_ms,
        peel_and_materialise_ms,
    )


def prepare_pruned_scenario(
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
    *,
    repeats: int,
    measure_memory: bool,
) -> PrunedScenario:
    """Measure deterministic preprocessing separately from path search."""

    total_samples: list[float] = []
    scoring_samples: list[float] = []
    peel_samples: list[float] = []
    fingerprints: list[str] = []
    last: PrunedScenario | None = None

    for _ in range(repeats):
        gc.collect()
        started = perf_counter()
        prepared, scoring_ms, peel_ms = _prepare_pruned_once(
            services,
            allocated,
            desired_stats,
        )
        total_samples.append((perf_counter() - started) * 1_000)
        scoring_samples.append(scoring_ms)
        peel_samples.append(peel_ms)
        fingerprints.append(
            prepared.diagnostics["reduced_graph_fingerprint_sha256"]
        )
        last = prepared

    peak_bytes: int | None = None
    if measure_memory:
        gc.collect()
        tracemalloc.start()
        memory_prepared, _, _ = _prepare_pruned_once(
            services,
            allocated,
            desired_stats,
        )
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if (
            memory_prepared.diagnostics["reduced_graph_fingerprint_sha256"]
            != fingerprints[-1]
        ):
            raise AssertionError("Memory run changed reduced graph")

    if last is None:  # pragma: no cover - repeats is validated by the CLI
        raise ValueError("preprocessing repeats must be positive")

    measurement = {
        "preprocessing_ms_samples": [
            round(value, 6) for value in total_samples
        ],
        "preprocessing_ms_median": round(
            statistics.median(total_samples),
            6,
        ),
        "node_scoring_ms_median": round(
            statistics.median(scoring_samples),
            6,
        ),
        "node_scoring_ms_samples": [
            round(value, 6) for value in scoring_samples
        ],
        "peel_and_materialise_ms_median": round(
            statistics.median(peel_samples),
            6,
        ),
        "peel_and_materialise_ms_samples": [
            round(value, 6) for value in peel_samples
        ],
        "peak_traced_memory_bytes": peak_bytes,
        "repeat_count": repeats,
        "deterministic_across_repeats": len(set(fingerprints)) == 1,
    }
    return PrunedScenario(last.services, last.diagnostics, measurement)


def rank(candidate: Mapping[str, Any]) -> tuple[float, float]:
    return float(candidate["score"]), float(candidate["efficiency"])


def is_strict_prefix(
    path: Sequence[str],
    other_path: Sequence[str],
) -> bool:
    return len(path) < len(other_path) and list(other_path[: len(path)]) == list(path)


def objective_filter(
    candidates: Sequence[Candidate],
    *,
    pool_size: int = DEFAULT_CANDIDATE_POOL_SIZE,
) -> list[Candidate]:
    """Mirror the endpoint's objective-aware prefix filter on a bounded pool."""

    pool = [dict(candidate) for candidate in candidates[:pool_size]]
    return [
        candidate
        for candidate in pool
        if not any(
            is_strict_prefix(candidate["path"], other["path"])
            and rank(other) > rank(candidate)
            for other in pool
        )
    ]


def candidate_from_exact(candidate) -> Candidate:
    return candidate.as_dict()


def candidate_fingerprint(candidates: Sequence[Candidate]) -> str:
    stable = [
        {
            "target": candidate["target"],
            "path": list(candidate["path"]),
            "cost": int(candidate["cost"]),
            "score": float(candidate["score"]),
            "efficiency": float(candidate["efficiency"]),
        }
        for candidate in candidates
    ]
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def response_fingerprint(candidates: Sequence[Candidate]) -> str:
    """Fingerprint the complete frontend-facing recommendation payload."""

    payload = json.dumps(
        list(candidates),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def diversity_metrics(
    candidates: Sequence[Candidate],
    allocated: set[str],
) -> dict[str, Any]:
    top = candidates[:10]
    node_sets = [
        set(str(node_id) for node_id in candidate["path"]) - allocated
        for candidate in top
    ]
    overlaps = [
        jaccard(node_sets[left], node_sets[right])
        for left in range(len(node_sets))
        for right in range(left + 1, len(node_sets))
    ]
    return {
        "top_k_count": len(top),
        "unique_targets": len({candidate["target"] for candidate in top}),
        "mean_pairwise_jaccard": (
            round(statistics.fmean(overlaps), 6) if overlaps else None
        ),
        "maximum_pairwise_jaccard": (
            round(max(overlaps), 6) if overlaps else None
        ),
    }


def validate_candidates(
    candidates: Sequence[Candidate],
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
    max_points: int,
) -> list[str]:
    errors: list[str] = []
    for index, candidate in enumerate(candidates):
        path = [str(node_id) for node_id in candidate["path"]]
        prefix = f"candidate[{index}]"
        if not path:
            errors.append(f"{prefix}: empty path")
            continue
        if path[0] not in allocated:
            errors.append(f"{prefix}: start is not allocated")
        if len(path) != len(set(path)):
            errors.append(f"{prefix}: path is not simple")
        if candidate["target"] != path[-1]:
            errors.append(f"{prefix}: target differs from endpoint")

        for left, right in zip(path, path[1:]):
            if right not in services.adjacency.get(left, ()):
                errors.append(f"{prefix}: non-edge {left}->{right}")
            if not services.pathfinder._is_traversable(right, allocated):
                errors.append(f"{prefix}: untraversable node {right}")

        expected_cost = services.evaluator.path_cost(path, allocated)
        expected_score = services.evaluator.score_path(
            path,
            allocated,
            desired_stats,
        )
        expected_efficiency = (
            expected_score / expected_cost if expected_cost else 0.0
        )
        if int(candidate["cost"]) != expected_cost:
            errors.append(f"{prefix}: cost mismatch")
        if expected_cost > max_points:
            errors.append(f"{prefix}: over budget")
        if not math.isclose(
            float(candidate["score"]),
            expected_score,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            errors.append(f"{prefix}: score mismatch")
        if not math.isclose(
            float(candidate["efficiency"]),
            expected_efficiency,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            errors.append(f"{prefix}: efficiency mismatch")
    return errors


def summarise_candidates(
    candidates: Sequence[Candidate],
    allocated: set[str],
) -> dict[str, Any]:
    top = list(candidates[:10])
    best = top[0] if top else None
    return {
        "best": (
            {
                "target": best["target"],
                "path": list(best["path"]),
                "cost": int(best["cost"]),
                "score": float(best["score"]),
                "efficiency": float(best["efficiency"]),
            }
            if best
            else None
        ),
        "fingerprint_sha256": candidate_fingerprint(top),
        "response_fingerprint_sha256": response_fingerprint(top),
        "diversity": diversity_metrics(top, allocated),
    }


def measure(
    operation: Callable[[], tuple[list[Candidate], dict[str, Any]]],
    *,
    repeats: int,
    measure_memory: bool,
) -> tuple[list[Candidate], dict[str, Any], dict[str, Any]]:
    timings: list[float] = []
    fingerprints: list[str] = []
    last_candidates: list[Candidate] = []
    last_diagnostics: dict[str, Any] = {}

    for _ in range(repeats):
        gc.collect()
        started = perf_counter()
        candidates, diagnostics = operation()
        timings.append((perf_counter() - started) * 1_000)
        fingerprints.append(response_fingerprint(candidates[:10]))
        last_candidates = candidates
        last_diagnostics = diagnostics

    peak_bytes: int | None = None
    if measure_memory:
        gc.collect()
        tracemalloc.start()
        memory_candidates, memory_diagnostics = operation()
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if response_fingerprint(memory_candidates[:10]) != fingerprints[-1]:
            raise AssertionError("Memory run changed candidate output")
        last_candidates = memory_candidates
        last_diagnostics = memory_diagnostics

    measurement = {
        "runtime_ms_samples": [round(value, 6) for value in timings],
        "runtime_ms_median": round(statistics.median(timings), 6),
        "search_ms_samples": [round(value, 6) for value in timings],
        "search_ms_median": round(statistics.median(timings), 6),
        "peak_traced_memory_bytes": peak_bytes,
        "repeat_count": repeats,
        "deterministic_across_repeats": (
            len(set(fingerprints)) == 1 if repeats > 1 else None
        ),
    }
    return last_candidates, last_diagnostics, measurement


def measure_interleaved(
    operations: Mapping[
        str,
        Callable[[], tuple[list[Candidate], dict[str, Any]]],
    ],
    *,
    repeats: int,
    measure_memory: bool,
) -> dict[
    str,
    tuple[list[Candidate], dict[str, Any], dict[str, Any]],
]:
    """Rotate comparable variants on each repeat to reduce order bias."""

    labels = list(operations)
    timings = {label: [] for label in labels}
    fingerprints = {label: [] for label in labels}
    last_candidates = {label: [] for label in labels}
    last_diagnostics = {label: {} for label in labels}
    execution_order: list[list[str]] = []

    for repeat_index in range(repeats):
        offset = repeat_index % len(labels)
        ordered_labels = labels[offset:] + labels[:offset]
        execution_order.append(ordered_labels)
        for label in ordered_labels:
            gc.collect()
            started = perf_counter()
            candidates, diagnostics = operations[label]()
            timings[label].append((perf_counter() - started) * 1_000)
            fingerprints[label].append(
                response_fingerprint(candidates[:10])
            )
            last_candidates[label] = candidates
            last_diagnostics[label] = diagnostics

    peak_bytes: dict[str, int | None] = {label: None for label in labels}
    if measure_memory:
        for label in labels:
            gc.collect()
            tracemalloc.start()
            candidates, diagnostics = operations[label]()
            _, peak_bytes[label] = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            if (
                response_fingerprint(candidates[:10])
                != fingerprints[label][-1]
            ):
                raise AssertionError(
                    f"Memory run changed candidate output for {label}"
                )
            last_candidates[label] = candidates
            last_diagnostics[label] = diagnostics

    results = {}
    for label in labels:
        samples = timings[label]
        measurement = {
            "runtime_ms_samples": [round(value, 6) for value in samples],
            "runtime_ms_median": round(statistics.median(samples), 6),
            "search_ms_samples": [round(value, 6) for value in samples],
            "search_ms_median": round(statistics.median(samples), 6),
            "peak_traced_memory_bytes": peak_bytes[label],
            "repeat_count": repeats,
            "deterministic_across_repeats": (
                len(set(fingerprints[label])) == 1
                if repeats > 1
                else None
            ),
            "timing_order_policy": (
                "four cache/pruning variants rotated by repeat"
            ),
            "execution_order_by_repeat": execution_order,
        }
        results[label] = (
            last_candidates[label],
            last_diagnostics[label],
            measurement,
        )
    return results


def current_operation(
    spec: StrategySpec,
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
    max_points: int,
    limit: int,
) -> Callable[[], tuple[list[Candidate], dict[str, Any]]]:
    optimizer_type = LegacyPrefixTreeOptimizer if spec.legacy_prefixes else TreeOptimizer

    def run() -> tuple[list[Candidate], dict[str, Any]]:
        optimizer = optimizer_type(services.pathfinder, services.evaluator)
        optimizer.STATES_PER_BUCKET = int(spec.states_per_bucket or 3)
        optimizer.MAX_EXPANDED_STATES = int(spec.max_expanded_states or 200_000)
        optimizer.CANDIDATE_POOL_SIZE = int(spec.candidate_pool_size)
        candidates = optimizer.recommend_paths(
            allocated,
            desired_stats,
            max_points=max_points,
            limit=limit,
        )
        return candidates, {
            "states_per_bucket": optimizer.STATES_PER_BUCKET,
            "max_expanded_states": optimizer.MAX_EXPANDED_STATES,
            "candidate_pool_size": optimizer.CANDIDATE_POOL_SIZE,
            "state_counters_available": False,
        }

    return run


def profile_current_node_scoring(
    spec: StrategySpec,
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
    max_points: int,
    limit: int,
    expected_candidates: Sequence[Candidate],
    *,
    repeats: int,
) -> dict[str, Any]:
    """Measure scorer calls in equivalent runs outside wall-clock samples."""

    instrumented, timer = instrument_services(services)
    operation = current_operation(
        spec,
        instrumented,
        allocated,
        desired_stats,
        max_points,
        limit,
    )
    timing_samples: list[float] = []
    call_samples: list[int] = []
    expected = list(expected_candidates)

    for _ in range(repeats):
        gc.collect()
        timer.reset()
        candidates, _ = operation()
        if candidates != expected:
            raise AssertionError(
                "Scorer profiling changed the recommendation payload"
            )
        timing_samples.append(timer.elapsed_ms)
        call_samples.append(timer.call_count)

    return {
        "search_node_scoring_ms_samples": [
            round(value, 6) for value in timing_samples
        ],
        "search_node_scoring_ms_median": round(
            statistics.median(timing_samples),
            6,
        ),
        "search_node_score_calls_samples": call_samples,
        "search_node_score_calls_median": statistics.median(call_samples),
        "scoring_timing_policy": (
            "delegate score_node time from equivalent profiling runs; nested "
            "inside search_ms and excluded from total addition"
        ),
    }


def current_probe_diagnostics(
    spec: StrategySpec,
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
    max_points: int,
    limit: int,
    production_candidates: Sequence[Candidate],
) -> dict[str, Any]:
    """Collect counters and prove the probe reproduced production output."""

    if spec.legacy_prefixes:
        return {
            "states_per_bucket": int(spec.states_per_bucket or 3),
            "max_expanded_states": int(
                spec.max_expanded_states or 200_000
            ),
            "candidate_pool_size": int(spec.candidate_pool_size),
            "state_counters_available": False,
            "probe_equivalence_checked": False,
        }

    probe = CurrentSearchProbe(services.pathfinder, services.evaluator)
    probe.STATES_PER_BUCKET = int(spec.states_per_bucket or 3)
    probe.MAX_EXPANDED_STATES = int(
        spec.max_expanded_states or 200_000
    )
    probe.CANDIDATE_POOL_SIZE = int(spec.candidate_pool_size)
    result = probe.recommend_paths_with_diagnostics(
        allocated,
        desired_stats,
        max_points=max_points,
        limit=limit,
    )
    probe_candidates = list(result.recommendations)
    if probe_candidates != list(production_candidates):
        raise AssertionError(
            "CurrentSearchProbe output differs from TreeOptimizer output"
        )
    diagnostics = asdict(result.diagnostics)
    diagnostics.update(
        {
            "states_per_bucket": probe.STATES_PER_BUCKET,
            "max_expanded_states": probe.MAX_EXPANDED_STATES,
            "candidate_pool_size": probe.CANDIDATE_POOL_SIZE,
            "state_counters_available": True,
            "probe_equivalence_checked": True,
        }
    )
    return diagnostics


def exact_operation(
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
    max_points: int,
    limit: int,
    max_expanded_states: int,
) -> Callable[[], tuple[list[Candidate], dict[str, Any]]]:
    def run() -> tuple[list[Candidate], dict[str, Any]]:
        solver = ExactPathSolver(services.pathfinder, services.evaluator)
        result = solver.solve(
            allocated,
            desired_stats,
            max_points=max_points,
            max_expanded_states=max_expanded_states,
        )
        raw_candidates = [
            candidate_from_exact(candidate)
            for candidate in result.candidates
        ]
        candidates = objective_filter(raw_candidates)[:limit]
        diagnostics = asdict(result.diagnostics)
        diagnostics.update(
            {
                "pruned_states": result.diagnostics.pruned_states,
                "raw_candidate_count": len(raw_candidates),
                "endpoint_normalization": (
                    "first 350 exact-ranked candidates, then the production "
                    "objective-aware prefix filter"
                ),
            }
        )
        return candidates, diagnostics

    return run


def priority_operation(
    spec: StrategySpec,
    services: ScenarioServices,
    allocated: set[str],
    desired_stats: DesiredStats,
    max_points: int,
    limit: int,
) -> Callable[[], tuple[list[Candidate], dict[str, Any]]]:
    def run() -> tuple[list[Candidate], dict[str, Any]]:
        search = OptimisticPrioritySearch(
            services.pathfinder,
            services.evaluator,
            PrioritySearchConfig(
                states_per_bucket=int(spec.states_per_bucket or 3),
                max_expanded_states=int(spec.max_expanded_states or 50_000),
                candidate_pool_size=int(spec.candidate_pool_size),
            ),
        )
        result = search.search(allocated, desired_stats, max_points)
        raw_candidates = [
            candidate_from_exact(candidate)
            for candidate in result.candidates
        ]
        candidates = objective_filter(raw_candidates)[:limit]
        diagnostics = asdict(result.diagnostics)
        diagnostics["endpoint_normalization"] = (
            "objective-aware prefix filter on the returned candidate pool"
        )
        return candidates, diagnostics

    return run


def strategy_specs(
    scenario: Mapping[str, Any],
    mode: str,
) -> list[StrategySpec]:
    specs = [
        StrategySpec(
            label="current_w3_cap200000",
            kind="current",
            states_per_bucket=3,
            max_expanded_states=200_000,
        ),
        StrategySpec(
            label="priority_w3_cap50000",
            kind="priority",
            states_per_bucket=3,
            max_expanded_states=50_000,
        ),
    ]
    if mode == "full":
        specs.extend(
            [
                StrategySpec(
                    label="current_w1_cap200000",
                    kind="current",
                    states_per_bucket=1,
                    max_expanded_states=200_000,
                ),
                StrategySpec(
                    label="current_w8_cap200000",
                    kind="current",
                    states_per_bucket=8,
                    max_expanded_states=200_000,
                ),
                StrategySpec(
                    label="priority_w4_cap50000",
                    kind="priority",
                    states_per_bucket=4,
                    max_expanded_states=50_000,
                ),
            ]
        )
    if scenario["id"] == "visited_set_width":
        specs.extend(
            [
                StrategySpec(
                    label="current_w4_cap200000",
                    kind="current",
                    states_per_bucket=4,
                    max_expanded_states=200_000,
                ),
                StrategySpec(
                    label="priority_w4_cap50000",
                    kind="priority",
                    states_per_bucket=4,
                    max_expanded_states=50_000,
                ),
            ]
        )
    if scenario.get("prefix_comparison"):
        specs.append(
            StrategySpec(
                label="legacy_prefix_w3_cap200000",
                kind="current",
                states_per_bucket=3,
                max_expanded_states=200_000,
                legacy_prefixes=True,
            )
        )
    for probe in scenario.get("current_probes", ()):
        specs.append(
            StrategySpec(
                label=probe["label"],
                kind="current",
                states_per_bucket=int(probe["states_per_bucket"]),
                max_expanded_states=int(probe["max_expanded_states"]),
            )
        )

    unique: dict[str, StrategySpec] = {}
    for spec in specs:
        unique.setdefault(spec.label, spec)
    return list(unique.values())


def compare_to_exact(
    run: dict[str, Any],
    exact_run: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if exact_run is None or exact_run.get("status") != "complete":
        return None
    exact_best = exact_run["result"]["best"]
    best = run["result"]["best"]
    if exact_best is None:
        return {
            "absolute_score_gap": 0.0 if best is None else None,
            "percentage_score_gap": 0.0 if best is None else None,
            "optimal_objective_in_top_k": best is None,
        }
    if best is None:
        return {
            "absolute_score_gap": exact_best["score"],
            "percentage_score_gap": 100.0,
            "optimal_objective_in_top_k": False,
        }

    gap = float(exact_best["score"]) - float(best["score"])
    percentage_gap = (
        100.0 * gap / abs(float(exact_best["score"]))
        if exact_best["score"] != 0
        else (0.0 if math.isclose(gap, 0.0) else None)
    )
    exact_rank = (
        float(exact_best["score"]),
        float(exact_best["efficiency"]),
    )
    top_candidates = run["_top_candidates"]
    optimum_present = any(
        math.isclose(float(candidate["score"]), exact_rank[0])
        and math.isclose(float(candidate["efficiency"]), exact_rank[1])
        for candidate in top_candidates
    )
    return {
        "absolute_score_gap": round(gap, 6),
        "percentage_score_gap": (
            round(percentage_gap, 6) if percentage_gap is not None else None
        ),
        "best_efficiency_gap_when_score_ties": (
            round(
                float(exact_best["efficiency"]) - float(best["efficiency"]),
                6,
            )
            if math.isclose(float(exact_best["score"]), float(best["score"]))
            else None
        ),
        "optimal_objective_in_top_k": optimum_present,
    }


def best_objective(record: Mapping[str, Any]) -> tuple[float, float] | None:
    best = record["result"]["best"]
    if best is None:
        return None
    return float(best["score"]), float(best["efficiency"])


def compare_pruned_to_unpruned(
    pruned_record: Mapping[str, Any],
    unpruned_record: Mapping[str, Any],
    pruned_candidates: Sequence[Candidate],
    unpruned_candidates: Sequence[Candidate],
) -> dict[str, Any]:
    pruned_paths = {
        tuple(str(node_id) for node_id in candidate["path"])
        for candidate in pruned_candidates[:10]
    }
    unpruned_paths = {
        tuple(str(node_id) for node_id in candidate["path"])
        for candidate in unpruned_candidates[:10]
    }
    union = pruned_paths | unpruned_paths
    comparison = {
        "best_objective_unchanged": (
            best_objective(pruned_record) == best_objective(unpruned_record)
        ),
        "best_score_change": (
            None
            if best_objective(pruned_record) is None
            or best_objective(unpruned_record) is None
            else round(
                best_objective(pruned_record)[0]
                - best_objective(unpruned_record)[0],
                6,
            )
        ),
        "identical_top_k_fingerprint": (
            pruned_record["result"]["fingerprint_sha256"]
            == unpruned_record["result"]["fingerprint_sha256"]
        ),
        "shared_exact_paths_in_top_k": len(pruned_paths & unpruned_paths),
        "top_k_path_jaccard": (
            round(len(pruned_paths & unpruned_paths) / len(union), 6)
            if union
            else 1.0
        ),
    }
    for field in ("generated_states", "expanded_states", "pruned_states"):
        before = unpruned_record.get("diagnostics", {}).get(field)
        after = pruned_record.get("diagnostics", {}).get(field)
        if isinstance(before, (int, float)) and isinstance(
            after, (int, float)
        ):
            reduction = before - after
            comparison[f"{field}_reduction"] = reduction
            comparison[f"{field}_reduction_percentage"] = (
                round(100.0 * reduction / before, 6) if before else 0.0
            )
    return comparison


def add_run_context(
    record: dict[str, Any],
    *,
    configuration: Mapping[str, Any],
    scenario: Mapping[str, Any],
    allocated: set[str],
    desired_stats: DesiredStats,
    pruning_enabled: bool,
    cache_enabled: bool = False,
    cache_applicable: bool = False,
) -> None:
    record.update(
        {
            "seed": int(configuration["seed"]),
            "point_budget": int(scenario["max_points"]),
            "allocated": sorted(allocated),
            "desired_stat_profile": scenario["profile"],
            "desired_stat_weights": [
                {
                    "stat_type": stat_type,
                    "modifier_type": modifier_type,
                    "weight": weight,
                }
                for (stat_type, modifier_type), weight in sorted(
                    desired_stats.items()
                )
            ],
            "pruning_enabled": pruning_enabled,
            "cache_enabled": cache_enabled,
            "cache_applicable": cache_applicable,
            "score_cache_enabled": cache_enabled,
            "score_cache_applicable": cache_applicable,
            "score_cache_mode": (
                "parsed_node_vectors"
                if cache_enabled and cache_applicable
                else (
                    "precomputed_synthetic_control"
                    if cache_enabled
                    else "uncached"
                )
            ),
        }
    )


def attach_preprocessing_measurement(
    measurement: dict[str, Any],
    preprocessing: Mapping[str, Any] | None,
) -> None:
    search_samples = [
        float(value) for value in measurement["search_ms_samples"]
    ]
    if preprocessing is None:
        preprocessing_samples = [0.0 for _ in search_samples]
        scoring_samples = [0.0 for _ in search_samples]
        materialisation_samples = [0.0 for _ in search_samples]
    else:
        raw_preprocessing = [
            float(value)
            for value in preprocessing["preprocessing_ms_samples"]
        ]
        raw_scoring = [
            float(value)
            for value in preprocessing["node_scoring_ms_samples"]
        ]
        raw_materialisation = [
            float(value)
            for value in preprocessing["peel_and_materialise_ms_samples"]
        ]
        if len(raw_preprocessing) == len(search_samples):
            preprocessing_samples = raw_preprocessing
            scoring_samples = raw_scoring
            materialisation_samples = raw_materialisation
        else:
            preprocessing_samples = [
                statistics.median(raw_preprocessing)
                for _ in search_samples
            ]
            scoring_samples = [
                statistics.median(raw_scoring) for _ in search_samples
            ]
            materialisation_samples = [
                statistics.median(raw_materialisation)
                for _ in search_samples
            ]

    total_samples = [
        preprocessing_ms + search_ms
        for preprocessing_ms, search_ms in zip(
            preprocessing_samples,
            search_samples,
        )
    ]
    measurement["preprocessing_ms_samples"] = [
        round(value, 6) for value in preprocessing_samples
    ]
    measurement["preprocessing_ms_median"] = round(
        statistics.median(preprocessing_samples),
        6,
    )
    measurement["preprocessing_node_scoring_ms_samples"] = [
        round(value, 6) for value in scoring_samples
    ]
    measurement["preprocessing_node_scoring_ms_median"] = round(
        statistics.median(scoring_samples),
        6,
    )
    measurement["pruning_and_graph_materialisation_ms_samples"] = [
        round(value, 6) for value in materialisation_samples
    ]
    measurement["pruning_and_graph_materialisation_ms_median"] = round(
        statistics.median(materialisation_samples),
        6,
    )
    # Explicit cache-experiment names; retain the older preprocessing names so
    # existing report consumers continue to work.
    measurement["pruning_node_scoring_ms_samples"] = list(
        measurement["preprocessing_node_scoring_ms_samples"]
    )
    measurement["pruning_node_scoring_ms_median"] = measurement[
        "preprocessing_node_scoring_ms_median"
    ]
    measurement["pruning_and_materialisation_ms_samples"] = list(
        measurement["pruning_and_graph_materialisation_ms_samples"]
    )
    measurement["pruning_and_materialisation_ms_median"] = measurement[
        "pruning_and_graph_materialisation_ms_median"
    ]

    search_scoring_raw = measurement.get("search_node_scoring_ms_samples")
    if search_scoring_raw is None:
        measurement["total_node_scoring_ms_samples"] = None
        measurement["total_node_scoring_ms_median"] = None
        measurement["search_excluding_node_scoring_ms_samples"] = None
        measurement["search_excluding_node_scoring_ms_median"] = None
    else:
        search_scoring_samples = [
            float(value) for value in search_scoring_raw
        ]
        if len(search_scoring_samples) != len(search_samples):
            search_scoring_samples = [
                statistics.median(search_scoring_samples)
                for _ in search_samples
            ]
        total_node_scoring_samples = [
            pruning_ms + search_ms
            for pruning_ms, search_ms in zip(
                scoring_samples,
                search_scoring_samples,
            )
        ]
        search_excluding_scoring_samples = [
            max(0.0, search_ms - scoring_ms)
            for search_ms, scoring_ms in zip(
                search_samples,
                search_scoring_samples,
            )
        ]
        measurement["total_node_scoring_ms_samples"] = [
            round(value, 6) for value in total_node_scoring_samples
        ]
        measurement["total_node_scoring_ms_median"] = round(
            statistics.median(total_node_scoring_samples),
            6,
        )
        measurement["search_excluding_node_scoring_ms_samples"] = [
            round(value, 6) for value in search_excluding_scoring_samples
        ]
        measurement["search_excluding_node_scoring_ms_median"] = round(
            statistics.median(search_excluding_scoring_samples),
            6,
        )
    measurement["total_wall_clock_ms_samples"] = [
        round(value, 6) for value in total_samples
    ]
    measurement["total_wall_clock_ms_median"] = round(
        statistics.median(total_samples),
        6,
    )


def execute_scenario(
    configuration: Mapping[str, Any],
    scenario: Mapping[str, Any],
    scenario_type: str,
    mode: str,
    repeats: int,
    measure_memory: bool,
) -> dict[str, Any]:
    services = (
        synthetic_services(scenario)
        if scenario_type == "synthetic"
        else real_services()
    )
    cache_applicable = scenario_type == "real_tree"
    cached_services = (
        real_cached_services() if cache_applicable else services
    )
    allocated = {str(node_id) for node_id in scenario["allocated"]}
    desired_stats = desired_stats_for(configuration, scenario["profile"])
    max_points = int(scenario["max_points"])
    limit = int(scenario.get("limit", 10))
    exact_limit = int(scenario.get("exact_max_expanded_states", 100_000))
    pruned = prepare_pruned_scenario(
        services,
        allocated,
        desired_stats,
        repeats=repeats,
        measure_memory=measure_memory,
    )
    cached_pruned = prepare_pruned_scenario(
        cached_services,
        allocated,
        desired_stats,
        repeats=repeats,
        measure_memory=measure_memory,
    )
    if cached_pruned.diagnostics != pruned.diagnostics:
        raise AssertionError(
            "Cached scoring changed pruning diagnostics or the reduced graph "
            f"in {scenario['id']}"
        )

    output: dict[str, Any] = {
        "id": scenario["id"],
        "type": scenario_type,
        "description": scenario["description"],
        "profile": scenario["profile"],
        "allocated": sorted(allocated),
        "max_points": max_points,
        "pruning": {
            "diagnostics": pruned.diagnostics,
            "measurement": pruned.measurement,
            "cached_measurement": cached_pruned.measurement,
            "cached_reduced_graph_matches_uncached": True,
        },
        "runs": [],
    }
    current_matrix_measurements: dict[
        str,
        tuple[list[Candidate], dict[str, Any], dict[str, Any]],
    ] = {}

    def preprocessing_record(
        enabled: bool,
        *,
        cache_enabled: bool = False,
    ) -> dict[str, Any]:
        if not enabled:
            return {
                "enabled": False,
                "diagnostics": None,
                "measurement": {
                    "preprocessing_ms_median": 0.0,
                },
            }
        selected = cached_pruned if cache_enabled else pruned
        return {
            "enabled": True,
            "diagnostics": selected.diagnostics,
            "measurement": selected.measurement,
        }

    def execute_current(
        *,
        spec: StrategySpec,
        target_services: ScenarioServices,
        pruning_enabled: bool,
        cache_enabled: bool,
        exact_reference: Mapping[str, Any] | None,
        experiment_variant: bool,
    ) -> tuple[dict[str, Any], list[Candidate]]:
        operation = current_operation(
            spec,
            target_services,
            allocated,
            desired_stats,
            max_points,
            limit,
        )
        premeasured = (
            current_matrix_measurements.get(spec.label)
            if experiment_variant
            else None
        )
        if premeasured is None:
            candidates, _, measurement = measure(
                operation,
                repeats=repeats,
                measure_memory=measure_memory,
            )
        else:
            candidates, _, measurement = premeasured
        diagnostics = current_probe_diagnostics(
            spec,
            target_services,
            allocated,
            desired_stats,
            max_points,
            limit,
            candidates,
        )
        if experiment_variant:
            measurement.update(
                profile_current_node_scoring(
                    spec,
                    target_services,
                    allocated,
                    desired_stats,
                    max_points,
                    limit,
                    candidates,
                    repeats=repeats,
                )
            )
        selected_preprocessing = (
            cached_pruned.measurement
            if pruning_enabled and cache_enabled
            else pruned.measurement if pruning_enabled else None
        )
        attach_preprocessing_measurement(
            measurement,
            selected_preprocessing,
        )
        errors = validate_candidates(
            candidates,
            target_services,
            allocated,
            desired_stats,
            max_points,
        )
        guarantee = (
            "legacy_heuristic_lifo_beam"
            if spec.legacy_prefixes
            else (
                "current_heuristic_lifo_beam_with_safe_preprocessing"
                if pruning_enabled
                else "current_heuristic_lifo_beam"
            )
        )
        record = {
            "strategy": spec.label,
            "guarantee": guarantee,
            "status": "complete",
            "diagnostics": diagnostics,
            "measurement": measurement,
            "preprocessing": preprocessing_record(
                pruning_enabled,
                cache_enabled=cache_enabled,
            ),
            "cache_experiment_variant": experiment_variant,
            "valid": not errors,
            "validation_errors": errors,
            "result": summarise_candidates(candidates, allocated),
            "_top_candidates": candidates[:10],
        }
        record["exact_comparison"] = compare_to_exact(
            record,
            dict(exact_reference) if exact_reference is not None else None,
        )
        add_run_context(
            record,
            configuration=configuration,
            scenario=scenario,
            allocated=allocated,
            desired_stats=desired_stats,
            pruning_enabled=pruning_enabled,
            cache_enabled=cache_enabled,
            cache_applicable=cache_applicable,
        )
        return record, candidates

    def execute_exact(
        *,
        label: str,
        target_services: ScenarioServices,
        pruning_enabled: bool,
    ) -> tuple[dict[str, Any], list[Candidate]]:
        exact_callable = exact_operation(
            target_services,
            allocated,
            desired_stats,
            max_points,
            limit,
            exact_limit,
        )
        search_started = perf_counter()
        try:
            candidates, diagnostics, measurement = measure(
                exact_callable,
                repeats=1 if max_points > 10 else repeats,
                measure_memory=measure_memory,
            )
            errors = validate_candidates(
                candidates,
                target_services,
                allocated,
                desired_stats,
                max_points,
            )
            record = {
                "strategy": label,
                "guarantee": "exact_after_complete_enumeration",
                "status": "complete",
                "diagnostics": diagnostics,
                "measurement": measurement,
                "preprocessing": preprocessing_record(pruning_enabled),
                "valid": not errors,
                "validation_errors": errors,
                "result": summarise_candidates(candidates, allocated),
                "_top_candidates": candidates[:10],
            }
        except ExactSearchLimitExceeded as error:
            elapsed_ms = (perf_counter() - search_started) * 1_000
            diagnostics = asdict(error.diagnostics)
            diagnostics["pruned_states"] = error.diagnostics.pruned_states
            measurement = {
                "runtime_ms_samples": [round(elapsed_ms, 6)],
                "runtime_ms_median": round(elapsed_ms, 6),
                "search_ms_samples": [round(elapsed_ms, 6)],
                "search_ms_median": round(elapsed_ms, 6),
                "peak_traced_memory_bytes": None,
                "repeat_count": 1,
                "deterministic_across_repeats": None,
            }
            candidates = []
            record = {
                "strategy": label,
                "guarantee": "no_partial_result_labelled_exact",
                "status": "limit_exceeded",
                "diagnostics": diagnostics,
                "measurement": measurement,
                "preprocessing": preprocessing_record(pruning_enabled),
                "valid": None,
                "validation_errors": [],
                "result": {
                    "best": None,
                    "fingerprint_sha256": None,
                    "diversity": None,
                },
                "_top_candidates": [],
            }

        attach_preprocessing_measurement(
            record["measurement"],
            pruned.measurement if pruning_enabled else None,
        )
        add_run_context(
            record,
            configuration=configuration,
            scenario=scenario,
            allocated=allocated,
            desired_stats=desired_stats,
            pruning_enabled=pruning_enabled,
        )
        return record, candidates

    exact_record, exact_candidates = execute_exact(
        label="exact",
        target_services=services,
        pruning_enabled=False,
    )
    output["runs"].append(exact_record)

    exact_pruned_record, exact_pruned_candidates = execute_exact(
        label="exact_pruned",
        target_services=pruned.services,
        pruning_enabled=True,
    )
    if (
        exact_record["status"] == "complete"
        and exact_pruned_record["status"] == "complete"
        and best_objective(exact_record) != best_objective(exact_pruned_record)
    ):
        raise AssertionError(
            f"Safe leaf pruning changed exact optimum in {scenario['id']}"
        )
    exact_pruned_record["comparison_to_unpruned"] = (
        compare_pruned_to_unpruned(
            exact_pruned_record,
            exact_record,
            exact_pruned_candidates,
            exact_candidates,
        )
        if exact_record["status"] == "complete"
        and exact_pruned_record["status"] == "complete"
        else None
    )
    output["runs"].append(exact_pruned_record)

    matrix_specs = {
        "current_w3_cap200000": StrategySpec(
            label="current_w3_cap200000",
            kind="current",
            states_per_bucket=3,
            max_expanded_states=200_000,
        ),
        "current_w3_cap200000_cached": StrategySpec(
            label="current_w3_cap200000_cached",
            kind="current",
            states_per_bucket=3,
            max_expanded_states=200_000,
        ),
        "current_w3_cap200000_pruned": StrategySpec(
            label="current_w3_cap200000_pruned",
            kind="current",
            states_per_bucket=3,
            max_expanded_states=200_000,
        ),
        "current_w3_cap200000_cached_pruned": StrategySpec(
            label="current_w3_cap200000_cached_pruned",
            kind="current",
            states_per_bucket=3,
            max_expanded_states=200_000,
        ),
    }
    matrix_services = {
        "current_w3_cap200000": services,
        "current_w3_cap200000_cached": cached_services,
        "current_w3_cap200000_pruned": pruned.services,
        "current_w3_cap200000_cached_pruned": cached_pruned.services,
    }
    current_matrix_measurements.update(
        measure_interleaved(
            {
                label: current_operation(
                    matrix_specs[label],
                    matrix_services[label],
                    allocated,
                    desired_stats,
                    max_points,
                    limit,
                )
                for label in matrix_specs
            },
            repeats=repeats,
            measure_memory=measure_memory,
        )
    )

    default_current_record: dict[str, Any] | None = None
    default_current_candidates: list[Candidate] = []
    for spec in strategy_specs(scenario, mode):
        if spec.kind == "current":
            record, candidates = execute_current(
                spec=spec,
                target_services=services,
                pruning_enabled=False,
                cache_enabled=False,
                exact_reference=exact_record,
                experiment_variant=(
                    spec.label == "current_w3_cap200000"
                ),
            )
        else:
            operation = priority_operation(
                spec,
                services,
                allocated,
                desired_stats,
                max_points,
                limit,
            )
            candidates, diagnostics, measurement = measure(
                operation,
                repeats=repeats,
                measure_memory=measure_memory,
            )
            attach_preprocessing_measurement(measurement, None)
            errors = validate_candidates(
                candidates,
                services,
                allocated,
                desired_stats,
                max_points,
            )
            record = {
                "strategy": spec.label,
                "guarantee": "heuristic_priority_beam",
                "status": "complete",
                "diagnostics": diagnostics,
                "measurement": measurement,
                "preprocessing": preprocessing_record(False),
                "cache_experiment_variant": False,
                "valid": not errors,
                "validation_errors": errors,
                "result": summarise_candidates(candidates, allocated),
                "_top_candidates": candidates[:10],
            }
            record["exact_comparison"] = compare_to_exact(
                record,
                exact_record,
            )
            add_run_context(
                record,
                configuration=configuration,
                scenario=scenario,
                allocated=allocated,
                desired_stats=desired_stats,
                pruning_enabled=False,
                cache_enabled=False,
                cache_applicable=cache_applicable,
            )

        output["runs"].append(record)
        if spec.label == "current_w3_cap200000":
            default_current_record = record
            default_current_candidates = candidates

    if default_current_record is None:  # pragma: no cover - fixed spec list
        raise AssertionError("Default current strategy was not executed")

    cached_spec = matrix_specs["current_w3_cap200000_cached"]
    cached_current_record, cached_current_candidates = execute_current(
        spec=cached_spec,
        target_services=cached_services,
        pruning_enabled=False,
        cache_enabled=True,
        exact_reference=exact_record,
        experiment_variant=True,
    )
    if cached_current_candidates != default_current_candidates:
        raise AssertionError(
            f"Cached scoring changed the baseline response in {scenario['id']}"
        )
    if (
        cached_current_record["diagnostics"]
        != default_current_record["diagnostics"]
    ):
        raise AssertionError(
            f"Cached scoring changed baseline state counters in {scenario['id']}"
        )
    cached_current_record["comparison_to_uncached"] = {
        "full_response_identical": True,
        "state_counters_identical": True,
    }
    output["runs"].append(cached_current_record)

    pruned_spec = matrix_specs["current_w3_cap200000_pruned"]
    pruned_current_record, pruned_candidates = execute_current(
        spec=pruned_spec,
        target_services=pruned.services,
        pruning_enabled=True,
        cache_enabled=False,
        exact_reference=exact_pruned_record,
        experiment_variant=True,
    )
    pruned_current_record["comparison_to_unpruned"] = (
        compare_pruned_to_unpruned(
            pruned_current_record,
            default_current_record,
            pruned_candidates,
            default_current_candidates,
        )
    )
    output["runs"].append(pruned_current_record)

    cached_pruned_spec = matrix_specs[
        "current_w3_cap200000_cached_pruned"
    ]
    cached_pruned_record, cached_pruned_candidates = execute_current(
        spec=cached_pruned_spec,
        target_services=cached_pruned.services,
        pruning_enabled=True,
        cache_enabled=True,
        exact_reference=exact_pruned_record,
        experiment_variant=True,
    )
    if cached_pruned_candidates != pruned_candidates:
        raise AssertionError(
            f"Cached scoring changed the pruned response in {scenario['id']}"
        )
    if (
        cached_pruned_record["diagnostics"]
        != pruned_current_record["diagnostics"]
    ):
        raise AssertionError(
            f"Cached scoring changed pruned state counters in {scenario['id']}"
        )
    cached_pruned_record["comparison_to_uncached"] = {
        "full_response_identical": True,
        "state_counters_identical": True,
        "reduced_graph_identical": True,
    }
    cached_pruned_record["comparison_to_unpruned"] = (
        compare_pruned_to_unpruned(
            cached_pruned_record,
            cached_current_record,
            cached_pruned_candidates,
            cached_current_candidates,
        )
    )
    output["runs"].append(cached_pruned_record)

    output["cache_experiment"] = {
        "variant_labels": [
            "current_w3_cap200000",
            "current_w3_cap200000_cached",
            "current_w3_cap200000_pruned",
            "current_w3_cap200000_cached_pruned",
        ],
        "cache_applicable": cache_applicable,
        "uncached_cached_payloads_identical": True,
        "uncached_cached_state_counters_identical": True,
        "uncached_cached_reduced_graphs_identical": True,
    }

    for run in output["runs"]:
        run.pop("_top_candidates", None)
    return output


def analyse_conditional_pruning(
    scenarios: Sequence[Mapping[str, Any]],
    *,
    locked_threshold: int = LOCKED_PRUNING_ESTIMATED_WORK_THRESHOLD,
) -> dict[str, Any]:
    """Evaluate preregistered estimated-work gates on cached real-tree pairs."""

    real_scenarios = [
        scenario for scenario in scenarios if scenario["type"] == "real_tree"
    ]

    def run_named(
        scenario: Mapping[str, Any],
        strategy: str,
    ) -> Mapping[str, Any]:
        return next(
            run for run in scenario["runs"] if run["strategy"] == strategy
        )

    candidate_thresholds = sorted(
        {*PRUNING_ESTIMATED_WORK_THRESHOLDS, locked_threshold}
    )
    evaluations: list[dict[str, Any]] = []
    for threshold in (*candidate_thresholds, None):
        rows: list[dict[str, Any]] = []
        for scenario in real_scenarios:
            baseline = run_named(
                scenario,
                "current_w3_cap200000_cached",
            )
            pruned = run_named(
                scenario,
                "current_w3_cap200000_cached_pruned",
            )
            graph_node_count = int(
                scenario["pruning"]["diagnostics"]["original_node_count"]
            )
            point_budget = int(scenario["max_points"])
            estimated_work = graph_node_count * point_budget
            selected = should_run_research_pruning(
                graph_node_count,
                point_budget,
                estimated_work_threshold=threshold,
            )
            baseline_ms = float(
                baseline["measurement"]["total_wall_clock_ms_median"]
            )
            pruned_ms = float(
                pruned["measurement"]["total_wall_clock_ms_median"]
            )
            selected_ms = pruned_ms if selected else baseline_ms
            saving_ms = baseline_ms - selected_ms
            material_margin_ms = max(1.0, baseline_ms * 0.05)
            comparison = pruned["comparison_to_unpruned"]
            generated_reduction = comparison.get(
                "generated_states_reduction",
                0,
            )
            rows.append(
                {
                    "scenario": scenario["id"],
                    "graph_node_count": graph_node_count,
                    "point_budget": point_budget,
                    "estimated_work": estimated_work,
                    "selected": selected,
                    "cached_baseline_total_ms": round(baseline_ms, 6),
                    "cached_pruned_total_ms": round(pruned_ms, 6),
                    "conditional_total_ms": round(selected_ms, 6),
                    "saving_ms": round(saving_ms, 6),
                    "material_margin_ms": round(material_margin_ms, 6),
                    "material_improvement": (
                        selected and saving_ms >= material_margin_ms
                    ),
                    "valid": bool(baseline["valid"] and pruned["valid"]),
                    "best_objective_unchanged": comparison[
                        "best_objective_unchanged"
                    ],
                    "top_k_fingerprint_changed": not comparison[
                        "identical_top_k_fingerprint"
                    ],
                    "generated_states_non_increasing": (
                        generated_reduction >= 0
                    ),
                }
            )

        selected_rows = [row for row in rows if row["selected"]]
        finite_threshold_eligible = bool(selected_rows) and all(
            row["valid"]
            and row["best_objective_unchanged"]
            and row["generated_states_non_increasing"]
            and row["material_improvement"]
            for row in selected_rows
        )
        evaluations.append(
            {
                "estimated_work_threshold": threshold,
                "rule": (
                    "never"
                    if threshold is None
                    else (
                        f"graph_node_count >= {MIN_GRAPH_NODES_FOR_PRUNING} "
                        "and graph_node_count * max_points >= "
                        f"{threshold}"
                    )
                ),
                "selected_scenario_count": len(selected_rows),
                "aggregate_saved_ms": round(
                    sum(row["saving_ms"] for row in rows),
                    6,
                ),
                "selected_win_count": sum(
                    row["saving_ms"] > 0 for row in selected_rows
                ),
                "selected_loss_count": sum(
                    row["saving_ms"] < 0 for row in selected_rows
                ),
                "selected_objective_change_count": sum(
                    not row["best_objective_unchanged"]
                    for row in selected_rows
                ),
                "selected_fingerprint_change_count": sum(
                    row["top_k_fingerprint_changed"]
                    for row in selected_rows
                ),
                "selected_invalid_count": sum(
                    not row["valid"] for row in selected_rows
                ),
                "worst_selected_regression_ms": round(
                    max(
                        [
                            0.0,
                            *(
                                -row["saving_ms"]
                                for row in selected_rows
                            ),
                        ]
                    ),
                    6,
                ),
                "faster_choice_capture_count": sum(
                    (row["selected"] and row["saving_ms"] > 0)
                    or (
                        not row["selected"]
                        and row["cached_baseline_total_ms"]
                        <= row["cached_pruned_total_ms"]
                    )
                    for row in rows
                ),
                "finite_threshold_meets_integration_bar": (
                    finite_threshold_eligible
                    if threshold is not None
                    else None
                ),
                "rows": rows,
            }
        )

    locked = next(
        item
        for item in evaluations
        if item["estimated_work_threshold"] == locked_threshold
    )
    locked_selected = int(locked["selected_scenario_count"])
    locked_passed = bool(
        locked["finite_threshold_meets_integration_bar"]
    )
    return {
        "status": (
            "locked_gate_passed"
            if locked_passed
            else (
                "locked_gate_not_exercised"
                if locked_selected == 0
                else "locked_gate_rejected"
            )
        ),
        "scope": (
            "real-tree cached-search variants only; synthetic scenarios are "
            "excluded from calibration"
        ),
        "metric": "graph_node_count * max_points",
        "candidate_thresholds": candidate_thresholds,
        "locked_candidate_threshold": locked_threshold,
        "minimum_graph_node_count": MIN_GRAPH_NODES_FOR_PRUNING,
        "integration_bar": (
            "Every selected real scenario must be valid, preserve the best "
            "objective, not increase generated states, and save at least "
            "max(1 ms, 5% of cached baseline total)."
        ),
        "recommended_rule": (
            locked["rule"]
            if locked_passed
            else "never"
        ),
        "threshold_origin": (
            "locked before this rerun from the prior full matrix; quick is a "
            "subset of full and is not independent validation"
        ),
        "conservative_tie_policy": (
            "retain the higher threshold when measured aggregate times tie"
        ),
        "evaluations": evaluations,
    }


def benchmark(
    configuration: Mapping[str, Any],
    *,
    mode: str,
    repeats: int,
    measure_memory: bool,
) -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = []
    for scenario_type, key in (
        ("synthetic", "synthetic"),
        ("real_tree", "real_tree"),
    ):
        for scenario in configuration[key]:
            if mode not in scenario.get("modes", ("quick", "full")):
                continue
            scenarios.append(
                execute_scenario(
                    configuration,
                    scenario,
                    scenario_type,
                    mode,
                    repeats,
                    measure_memory,
                )
            )

    all_runs = [run for scenario in scenarios for run in scenario["runs"]]
    comparable_runs = [
        run
        for run in all_runs
        if run.get("exact_comparison") is not None
    ]
    exact_pruned_runs = [
        run
        for run in all_runs
        if run["strategy"] == "exact_pruned"
        and run.get("comparison_to_unpruned") is not None
    ]
    current_pruned_runs = [
        run
        for run in all_runs
        if run["strategy"] == "current_w3_cap200000_pruned"
    ]
    current_pruning_comparisons = [
        run["comparison_to_unpruned"] for run in current_pruned_runs
    ]
    current_pruning_pairs = [
        (
            next(
                run
                for run in scenario["runs"]
                if run["strategy"] == "current_w3_cap200000"
            ),
            next(
                run
                for run in scenario["runs"]
                if run["strategy"] == "current_w3_cap200000_pruned"
            ),
        )
        for scenario in scenarios
    ]
    scenario_pruning = [scenario["pruning"] for scenario in scenarios]
    cache_experiment_runs = [
        run for run in all_runs if run.get("cache_experiment_variant")
    ]
    real_cache_pairs = [
        (
            next(
                run
                for run in scenario["runs"]
                if run["strategy"] == "current_w3_cap200000"
            ),
            next(
                run
                for run in scenario["runs"]
                if run["strategy"] == "current_w3_cap200000_cached"
            ),
            next(
                run
                for run in scenario["runs"]
                if run["strategy"] == "current_w3_cap200000_pruned"
            ),
            next(
                run
                for run in scenario["runs"]
                if run["strategy"]
                == "current_w3_cap200000_cached_pruned"
            ),
        )
        for scenario in scenarios
        if scenario["type"] == "real_tree"
    ]
    configured_threshold = configuration.get("pruning_gate", {}).get(
        "estimated_work_threshold"
    )
    locked_threshold = (
        int(configured_threshold)
        if configured_threshold is not None
        else LOCKED_PRUNING_ESTIMATED_WORK_THRESHOLD
    )
    conditional_pruning = analyse_conditional_pruning(
        scenarios,
        locked_threshold=locked_threshold,
    )
    conditional_pruning["locked_threshold_source"] = (
        "configuration"
        if configured_threshold is not None
        else "prior_full_matrix"
    )
    if configured_threshold is not None:
        conditional_pruning["threshold_origin"] = (
            "fixed estimated_work_threshold from benchmark configuration"
        )
    gate_iterations = 100_000
    gate_started = perf_counter()
    gate_checksum = 0
    for index in range(gate_iterations):
        gate_checksum += should_run_research_pruning(
            1_997,
            (5, 10, 15, 20)[index % 4],
            estimated_work_threshold=locked_threshold,
        )
    gate_elapsed_ms = (perf_counter() - gate_started) * 1_000
    return {
        "schema_version": 3,
        "mode": mode,
        "seed": configuration["seed"],
        "context": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python_hash_seed": os.environ.get(
                "PYTHONHASHSEED", "randomized"
            ),
            "tree_data": configuration["tree_data"],
            "tree_sha256": hashlib.sha256(
                (REPOSITORY_ROOT / configuration["tree_data"]).read_bytes()
            ).hexdigest(),
            "cached_score_vector_build": real_cache_build_measurement(),
            "pruning_gate_microbenchmark": {
                "iterations": gate_iterations,
                "elapsed_ms": round(gate_elapsed_ms, 6),
                "mean_microseconds_per_decision": round(
                    gate_elapsed_ms * 1_000 / gate_iterations,
                    6,
                ),
                "checksum": gate_checksum,
            },
        },
        "measurement_note": (
            "runtime_ms_* and peak_traced_memory_bytes are nondeterministic "
            "machine-local observations; scores, paths, gaps, validity, "
            "fingerprints, and search counters are deterministic inputs/results"
        ),
        "methodology": {
            "objective": "maximum total score, then maximum score per point",
            "leaf_pruning": (
                "Repeatedly remove unallocated, non-required degree-zero-or-one "
                "nodes whose production query score is non-positive. Class "
                "starts, keystones, jewel/expansion nodes, multiple-choice "
                "nodes, excluded special kinds, and unknown nodes are protected."
            ),
            "adjacency_order": (
                "The reduced graph preserves baseline node and retained-neighbour "
                "order so bounded LIFO differences come from removals, not sorting."
            ),
            "preprocessing_timing": (
                "Query-wide production node scoring, queue peeling, and reduced "
                "graph materialisation are timed separately. Preprocessing and "
                "search samples are added per repeat before taking the total "
                "median. Search-node scoring is measured in equivalent profiling "
                "runs and is nested within, not added to, search wall time."
            ),
            "cache_timing": (
                "Cached vectors are eagerly built once outside query timings; "
                "synthetic fixed-score scenarios label caching as inapplicable."
            ),
            "exact_top_k_normalization": (
                "Exact enumeration retains all positive prefixes and may "
                "traverse allocated nodes at the budget. Endpoint comparisons "
                "use the first 350 exact-ranked paths plus the production "
                "objective-aware prefix filter. Optimal-in-top-k compares "
                "objective rank, not path identity."
            ),
            "memory": (
                "One additional tracemalloc run per strategy"
                if measure_memory
                else "disabled"
            ),
            "repeats": repeats,
        },
        "summary": {
            "scenario_count": len(scenarios),
            "strategy_run_count": len(all_runs),
            "cache_experiment_run_count": len(cache_experiment_runs),
            "cache_equivalence_failure_count": sum(
                not scenario["cache_experiment"][
                    "uncached_cached_payloads_identical"
                ]
                or not scenario["cache_experiment"][
                    "uncached_cached_state_counters_identical"
                ]
                or not scenario["cache_experiment"][
                    "uncached_cached_reduced_graphs_identical"
                ]
                for scenario in scenarios
            ),
            "real_cached_unpruned_search_time_improvement_count": sum(
                cached["measurement"]["search_ms_median"]
                < uncached["measurement"]["search_ms_median"]
                for uncached, cached, _, _ in real_cache_pairs
            ),
            "real_cached_unpruned_total_ms_saved": round(
                sum(
                    uncached["measurement"]["total_wall_clock_ms_median"]
                    - cached["measurement"]["total_wall_clock_ms_median"]
                    for uncached, cached, _, _ in real_cache_pairs
                ),
                6,
            ),
            "real_cached_pruned_total_time_improvement_count": sum(
                cached_pruned["measurement"]["total_wall_clock_ms_median"]
                < cached["measurement"]["total_wall_clock_ms_median"]
                for _, cached, _, cached_pruned in real_cache_pairs
            ),
            "invalid_run_count": sum(run.get("valid") is False for run in all_runs),
            "exact_limit_exceeded_count": sum(
                run["strategy"].startswith("exact")
                and run["status"] == "limit_exceeded"
                for run in all_runs
            ),
            "exact_pruning_comparison_count": len(exact_pruned_runs),
            "exact_pruning_optimum_mismatch_count": sum(
                not run["comparison_to_unpruned"][
                    "best_objective_unchanged"
                ]
                for run in exact_pruned_runs
            ),
            "pruning_removed_nodes_across_scenario_queries": sum(
                item["diagnostics"]["removed_node_count"]
                for item in scenario_pruning
            ),
            "pruning_removed_nothing_scenario_count": sum(
                item["diagnostics"]["removed_node_count"] == 0
                for item in scenario_pruning
            ),
            "current_pruning_comparison_count": len(
                current_pruning_comparisons
            ),
            "current_pruning_best_objective_change_count": sum(
                not comparison["best_objective_unchanged"]
                for comparison in current_pruning_comparisons
            ),
            "current_pruning_best_score_improvement_count": sum(
                (comparison["best_score_change"] or 0) > 0
                for comparison in current_pruning_comparisons
            ),
            "current_pruning_best_score_regression_count": sum(
                (comparison["best_score_change"] or 0) < 0
                for comparison in current_pruning_comparisons
            ),
            "current_pruning_generated_state_reduction_count": sum(
                comparison.get("generated_states_reduction", 0) > 0
                for comparison in current_pruning_comparisons
            ),
            "current_pruning_search_time_improvement_count": sum(
                pruned_run["measurement"]["search_ms_median"]
                < baseline_run["measurement"]["search_ms_median"]
                for baseline_run, pruned_run in current_pruning_pairs
            ),
            "current_pruning_total_time_improvement_count": sum(
                pruned_run["measurement"]["total_wall_clock_ms_median"]
                < baseline_run["measurement"]["total_wall_clock_ms_median"]
                for baseline_run, pruned_run in current_pruning_pairs
            ),
            "compared_heuristic_run_count": len(comparable_runs),
            "heuristic_score_gap_count": sum(
                (run["exact_comparison"]["absolute_score_gap"] or 0) > 0
                for run in comparable_runs
            ),
            "heuristic_optimum_missing_top_k_count": sum(
                not run["exact_comparison"]["optimal_objective_in_top_k"]
                for run in comparable_runs
            ),
        },
        "conditional_pruning": conditional_pruning,
        "scenarios": scenarios,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="Run the quick scenario set")
    mode.add_argument("--full", action="store_true", help="Run all scenarios and sweeps")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS,
        help="Scenario definition JSON",
    )
    parser.add_argument("--output", type=Path, help="Write JSON to this path")
    parser.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="Timed repeats per bounded strategy (default: 2)",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="Skip the additional tracemalloc run",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.repeats <= 0:
        raise SystemExit("--repeats must be positive")
    mode = "full" if args.full else "quick"
    configuration = load_configuration(args.scenarios.resolve())
    report = benchmark(
        configuration,
        mode=mode,
        repeats=args.repeats,
        measure_memory=not args.no_memory,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(
            f"Wrote {report['summary']['strategy_run_count']} runs across "
            f"{report['summary']['scenario_count']} scenarios to {output}"
        )
    else:
        sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
