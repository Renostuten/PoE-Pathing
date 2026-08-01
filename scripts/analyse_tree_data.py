#!/usr/bin/env python3
"""Generate a deterministic, concise analysis of the passive-tree export.

The script deliberately imports the production graph builder, node lookup,
traversal rules, parser, and scorer.  It adds the repository's ``src`` directory
to ``sys.path`` so it can be run from the repository root without setting
``PYTHONPATH``:

    python scripts/analyse_tree_data.py
    python scripts/analyse_tree_data.py --format json

Markdown defaults to ``docs/tree-data-analysis.md``; JSON defaults to stdout.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from contextlib import redirect_stdout
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
RAW_TREE_DIR = SRC_ROOT / "poe_pathing" / "data" / "raw"
DEFAULT_REPORT_PATH = REPO_ROOT / "docs" / "tree-data-analysis.md"
DEFAULT_TREE_CANDIDATES = tuple(
    sorted(RAW_TREE_DIR.glob("*.json"), key=lambda path: path.name)
)
DEFAULT_TREE_PATH = (
    DEFAULT_TREE_CANDIDATES[0]
    if len(DEFAULT_TREE_CANDIDATES) == 1
    else None
)

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from poe_pathing.calculation.stat_parser import StatParser  # noqa: E402
from poe_pathing.calculation.stat_scorer import StatScorer  # noqa: E402
from poe_pathing.graph.build import is_drawable_node, load_adj  # noqa: E402
from poe_pathing.graph.pathfinder import PathFinder  # noqa: E402
from poe_pathing.tree.node_lookup import NodeLookup  # noqa: E402


JsonObject = dict[str, Any]
Adjacency = dict[str, set[str]]
Edge = tuple[str, str]

NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
PATTERN_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
WHITESPACE_RE = re.compile(r"\s+")
DISTANCE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0", 0, 0),
    ("1-5", 1, 5),
    ("6-10", 6, 10),
    ("11-15", 11, 15),
    ("16-20", 16, 20),
    ("21-30", 21, 30),
    ("31-40", 31, 40),
    ("41-50", 41, 50),
    ("51-60", 51, 60),
    ("61+", 61, None),
)

REPRESENTATIVE_PROFILES: dict[str, tuple[tuple[str, str], ...]] = {
    "attributes": (
        ("strength", "flat"),
        ("dexterity", "flat"),
        ("intelligence", "flat"),
    ),
    "elemental_caster": (
        ("fire_damage", "increased_percent"),
        ("cold_damage", "increased_percent"),
        ("lightning_damage", "increased_percent"),
        ("cast_speed", "increased_percent"),
        ("maximum_mana", "increased_percent"),
        ("intelligence", "flat"),
    ),
    "life_and_resistances": (
        ("maximum_life", "flat"),
        ("maximum_life", "increased_percent"),
        ("fire_resistance", "flat_percent"),
        ("cold_resistance", "flat_percent"),
        ("lightning_resistance", "flat_percent"),
        ("chaos_resistance", "flat_percent"),
    ),
    "physical_attacker": (
        ("physical_damage", "increased_percent"),
        ("attack_speed", "increased_percent"),
        ("strength", "flat"),
        ("dexterity", "flat"),
    ),
}


def node_sort_key(node_id: str) -> tuple[int, int | str]:
    """Sort numeric export IDs numerically and any unexpected IDs lexically."""

    try:
        return (0, int(node_id))
    except ValueError:
        return (1, node_id)


def sorted_node_ids(node_ids: Iterable[str]) -> list[str]:
    return sorted(node_ids, key=node_sort_key)


def percentage(numerator: int | float, denominator: int | float) -> float:
    if denominator == 0:
        return 0.0
    return round(100.0 * numerator / denominator, 2)


def rounded(value: float, digits: int = 4) -> float:
    result = round(value, digits)
    return 0.0 if result == -0.0 else result


def numeric_histogram(values: Iterable[int]) -> dict[str, int]:
    counts = Counter(values)
    return {str(value): counts[value] for value in sorted(counts)}


def distribution_summary(values: Sequence[int | float]) -> JsonObject:
    """Return deterministic nearest-rank quantiles for a concise report."""

    if not values:
        return {"count": 0}

    ordered = sorted(values)

    def nearest_rank(proportion: float) -> int | float:
        index = max(0, math.ceil(proportion * len(ordered)) - 1)
        return ordered[index]

    def clean(value: int | float) -> int | float:
        if isinstance(value, int):
            return value
        return rounded(value)

    return {
        "count": len(ordered),
        "minimum": clean(ordered[0]),
        "p25_nearest_rank": clean(nearest_rank(0.25)),
        "median_nearest_rank": clean(nearest_rank(0.5)),
        "p75_nearest_rank": clean(nearest_rank(0.75)),
        "p90_nearest_rank": clean(nearest_rank(0.9)),
        "maximum": clean(ordered[-1]),
        "mean": rounded(statistics.fmean(ordered)),
    }


def canonical_edge(first: str, second: str) -> Edge:
    """Return one deterministic representation of an undirected edge."""

    ordered = sorted((first, second), key=node_sort_key)
    return (ordered[0], ordered[1])


def edge_sort_key(edge: Edge) -> tuple[tuple[int, int | str], tuple[int, int | str]]:
    return (node_sort_key(edge[0]), node_sort_key(edge[1]))


def load_tree(path: Path) -> JsonObject:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data.get("nodes"), dict) or not isinstance(
        data.get("groups"), dict
    ):
        raise ValueError("Passive-tree export must contain object-valued nodes and groups")

    return data


def build_filtered_graph(
    data: JsonObject, tree_path: Path
) -> tuple[list[str], Adjacency, dict[str, list[str]], JsonObject]:
    nodes: dict[str, JsonObject] = data["nodes"]
    groups: dict[str, JsonObject] = data["groups"]
    drawable_ids = sorted_node_ids(
        node_id
        for node_id, node in nodes.items()
        if is_drawable_node(node, groups)
    )
    drawable_set = set(drawable_ids)

    # This is the graph implementation used by the application.  Adding empty
    # sets below makes drawable-but-isolated nodes explicit for graph metrics.
    production_adj = load_adj(str(tree_path))
    adjacency: Adjacency = {
        node_id: set(production_adj.get(node_id, ())) for node_id in drawable_ids
    }

    unexpected_nodes = sorted_node_ids(set(production_adj) - drawable_set)
    unexpected_neighbours = sorted_node_ids(
        {
            neighbour
            for neighbours in production_adj.values()
            for neighbour in neighbours
            if neighbour not in drawable_set
        }
    )
    asymmetric_pairs = sorted(
        (
            (node_id, neighbour)
            for node_id, neighbours in adjacency.items()
            for neighbour in neighbours
            if node_id not in adjacency.get(neighbour, set())
        ),
        key=lambda pair: (node_sort_key(pair[0]), node_sort_key(pair[1])),
    )

    validation = {
        "unexpected_adjacency_nodes": unexpected_nodes,
        "unexpected_neighbours": unexpected_neighbours,
        "asymmetric_pair_count": len(asymmetric_pairs),
        "self_loop_count": sum(
            1 for node_id, neighbours in adjacency.items() if node_id in neighbours
        ),
    }
    return drawable_ids, adjacency, production_adj, validation


def connected_components(
    node_ids: Sequence[str], adjacency: Mapping[str, set[str]]
) -> list[list[str]]:
    unseen = set(node_ids)
    components: list[list[str]] = []

    for start in node_ids:
        if start not in unseen:
            continue

        unseen.remove(start)
        queue = deque([start])
        component: list[str] = []

        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbour in sorted_node_ids(adjacency.get(current, set())):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    queue.append(neighbour)

        components.append(sorted_node_ids(component))

    components.sort(
        key=lambda component: (
            -len(component),
            node_sort_key(component[0]),
        )
    )
    return components


def count_edges(node_ids: Sequence[str], adjacency: Mapping[str, set[str]]) -> int:
    rank = {node_id: index for index, node_id in enumerate(node_ids)}
    return sum(
        1
        for node_id in node_ids
        for neighbour in adjacency.get(node_id, set())
        if rank.get(neighbour, -1) > rank[node_id]
    )


def node_display_name(nodes: Mapping[str, JsonObject], node_id: str) -> str:
    return str(nodes[node_id].get("name") or "<unnamed>")


def graph_metrics(
    data: JsonObject,
    drawable_ids: Sequence[str],
    adjacency: Adjacency,
    production_adj: Mapping[str, list[str]],
    validation: JsonObject,
) -> tuple[JsonObject, list[list[str]]]:
    nodes: dict[str, JsonObject] = data["nodes"]
    components = connected_components(drawable_ids, adjacency)
    degrees = {node_id: len(adjacency[node_id]) for node_id in drawable_ids}
    edge_count = count_edges(drawable_ids, adjacency)

    high_degree = sorted(
        drawable_ids,
        key=lambda node_id: (-degrees[node_id], node_sort_key(node_id)),
    )[:12]
    high_degree_records = [
        {
            "id": node_id,
            "name": node_display_name(nodes, node_id),
            "degree": degrees[node_id],
            "group": str(nodes[node_id].get("group")),
        }
        for node_id in high_degree
    ]

    production_keys = set(production_adj)
    isolated = [
        node_id for node_id in drawable_ids if not adjacency[node_id]
    ]
    return (
        {
            "drawable_node_count": len(drawable_ids),
            "production_adjacency_key_count": len(production_adj),
            "drawable_nodes_absent_from_adjacency": len(
                set(drawable_ids) - production_keys
            ),
            "isolated_drawable_node_count": len(isolated),
            "isolated_drawable_nodes": [
                {
                    "id": node_id,
                    "name": node_display_name(nodes, node_id),
                }
                for node_id in isolated
            ],
            "undirected_edge_count": edge_count,
            "component_count": len(components),
            "component_size_histogram": numeric_histogram(
                len(component) for component in components
            ),
            "largest_component_sizes": [
                len(component) for component in components[:12]
            ],
            "degree_histogram": numeric_histogram(degrees.values()),
            "average_degree": rounded(
                sum(degrees.values()) / len(drawable_ids) if drawable_ids else 0.0
            ),
            "maximum_degree": max(degrees.values(), default=0),
            "high_degree_nodes": high_degree_records,
            **validation,
        },
        components,
    )


def schema_metrics(data: JsonObject) -> JsonObject:
    nodes: dict[str, JsonObject] = data["nodes"]
    groups: dict[str, JsonObject] = data["groups"]
    node_key_union = sorted(
        {key for node in nodes.values() for key in node}
    )
    group_key_union = sorted(
        {key for group in groups.values() for key in group}
    )
    relevant_fields = (
        "group",
        "in",
        "out",
        "stats",
        "classStartIndex",
        "ascendancyName",
        "isAscendancyStart",
        "isMastery",
        "isProxy",
        "isNotable",
        "isKeystone",
        "isJewelSocket",
        "expansionJewel",
    )

    return {
        "top_level_keys": sorted(data),
        "top_level_types": {
            key: type(data[key]).__name__ for key in sorted(data)
        },
        "node_field_union": node_key_union,
        "group_field_union": group_key_union,
        "relevant_node_field_presence": {
            field: sum(1 for node in nodes.values() if field in node)
            for field in relevant_fields
        },
        "raw_node_count": len(nodes),
        "raw_group_count": len(groups),
    }


def node_type_flags(
    node_id: str,
    node: JsonObject,
    jewel_slot_ids: set[str],
) -> dict[str, bool]:
    ascendancy = bool(
        node.get("ascendancyName") is not None
        or node.get("isAscendancyStart", False)
    )
    jewel_related = bool(
        node.get("isJewelSocket", False)
        or node.get("expansionJewel") is not None
        or node_id in jewel_slot_ids
        or "jewel socket" in str(node.get("name", "")).lower()
    )
    class_start = node.get("classStartIndex") is not None
    flags = {
        "notable": bool(node.get("isNotable", False)),
        "keystone": bool(node.get("isKeystone", False)),
        "mastery": bool(node.get("isMastery", False)),
        "proxy": bool(node.get("isProxy", False)),
        "ascendancy": ascendancy,
        "jewel_related": jewel_related,
        "jewel_socket_flag": bool(node.get("isJewelSocket", False)),
        "expansion_jewel": node.get("expansionJewel") is not None,
        "class_start": class_start,
        "bloodline": bool(node.get("isBloodline", False)),
    }
    flags["normal"] = not any(flags.values())
    return flags


def type_metrics(data: JsonObject, drawable_ids: Sequence[str]) -> JsonObject:
    nodes: dict[str, JsonObject] = data["nodes"]
    jewel_slot_ids = {str(node_id) for node_id in data.get("jewelSlots", [])}
    drawable_set = set(drawable_ids)
    categories = (
        "normal",
        "notable",
        "keystone",
        "mastery",
        "proxy",
        "ascendancy",
        "jewel_related",
        "jewel_socket_flag",
        "expansion_jewel",
        "class_start",
        "bloodline",
    )
    raw_counts = Counter({category: 0 for category in categories})
    drawable_counts = Counter({category: 0 for category in categories})

    for node_id in sorted_node_ids(nodes):
        flags = node_type_flags(node_id, nodes[node_id], jewel_slot_ids)
        for category, enabled in flags.items():
            if enabled:
                raw_counts[category] += 1
                if node_id in drawable_set:
                    drawable_counts[category] += 1

    return {
        "definitions": {
            "ascendancy": "ascendancyName is present or isAscendancyStart is true",
            "jewel_related": (
                "isJewelSocket, expansionJewel, top-level jewelSlots membership, "
                "or a name containing 'Jewel Socket'"
            ),
            "normal": (
                "none of the reported notable/keystone/mastery/proxy/ascendancy/"
                "jewel/class-start/bloodline categories"
            ),
            "overlap": "All categories except normal may overlap.",
        },
        "raw": {category: raw_counts[category] for category in categories},
        "drawable": {
            category: drawable_counts[category] for category in categories
        },
    }


def group_contains_cycle(
    members: Sequence[str], adjacency: Mapping[str, set[str]]
) -> bool:
    member_set = set(members)
    visited: set[str] = set()

    for start in members:
        if start in visited:
            continue
        stack: list[tuple[str, str | None]] = [(start, None)]
        visited.add(start)

        while stack:
            current, parent = stack.pop()
            for neighbour in adjacency.get(current, set()):
                if neighbour not in member_set:
                    continue
                if neighbour not in visited:
                    visited.add(neighbour)
                    stack.append((neighbour, current))
                elif neighbour != parent:
                    return True
    return False


def group_label(
    members: Sequence[str], nodes: Mapping[str, JsonObject]
) -> str:
    preferred = [
        node_id
        for node_id in members
        if nodes[node_id].get("isNotable") or nodes[node_id].get("isKeystone")
    ]
    chosen = preferred[0] if preferred else members[0]
    return node_display_name(nodes, chosen)


def group_metrics(
    data: JsonObject,
    drawable_ids: Sequence[str],
    adjacency: Mapping[str, set[str]],
    scorer: StatScorer,
    articulation_points: set[str],
    bridges: set[Edge],
) -> JsonObject:
    nodes: dict[str, JsonObject] = data["nodes"]
    groups: dict[str, JsonObject] = data["groups"]
    by_group: dict[str, list[str]] = defaultdict(list)

    for node_id in drawable_ids:
        group_id = str(nodes[node_id]["group"])
        by_group[group_id].append(node_id)

    for members in by_group.values():
        members.sort(key=node_sort_key)

    node_group = {
        node_id: str(nodes[node_id]["group"]) for node_id in drawable_ids
    }
    node_scores_by_profile = {
        profile_name: {
            node_id: scorer.score_node(
                node_id,
                {key: 1.0 for key in profile_keys},
            )
            for node_id in drawable_ids
        }
        for profile_name, profile_keys in REPRESENTATIVE_PROFILES.items()
    }
    positive_nodes_by_profile = {
        profile_name: {
            node_id for node_id, score in scores.items() if score > 0
        }
        for profile_name, scores in node_scores_by_profile.items()
    }

    records: list[JsonObject] = []
    nontrivial_densities: list[float] = []
    total_internal_edges = 0
    groups_with_cycle = 0
    for group_id in sorted(by_group, key=lambda value: int(value)):
        members = by_group[group_id]
        member_set = set(members)
        internal_edges = sum(
            1
            for node_id in members
            for neighbour in adjacency.get(node_id, set())
            if neighbour in member_set
        ) // 2
        total_internal_edges += internal_edges
        possible_edges = len(members) * (len(members) - 1) // 2
        density = (
            internal_edges / possible_edges if possible_edges else 0.0
        )
        if len(members) >= 2:
            nontrivial_densities.append(density)
        has_cycle = group_contains_cycle(members, adjacency)
        groups_with_cycle += int(has_cycle)

        boundary_nodes: set[str] = set()
        boundary_edges: set[Edge] = set()
        external_attachment_nodes: set[str] = set()
        neighbouring_groups: set[str] = set()
        for node_id in members:
            for neighbour in adjacency.get(node_id, set()):
                neighbour_group = node_group[neighbour]
                if neighbour_group == group_id:
                    continue
                boundary_nodes.add(node_id)
                boundary_edges.add(canonical_edge(node_id, neighbour))
                external_attachment_nodes.add(neighbour)
                neighbouring_groups.add(neighbour_group)

        boundary_ratio = len(boundary_nodes) / len(members)
        edge_boundary_ratio = (
            len(boundary_edges) / internal_edges
            if internal_edges > 0
            else None
        )
        positive_counts = {
            profile_name: len(member_set.intersection(positive_nodes))
            for profile_name, positive_nodes in positive_nodes_by_profile.items()
        }
        profile_score_values = {
            profile_name: [scores[node_id] for node_id in members]
            for profile_name, scores in node_scores_by_profile.items()
        }
        profile_score_summaries = {
            profile_name: {
                "total_score": rounded(sum(values)),
                "mean_node_score": rounded(statistics.fmean(values)),
                "median_node_score": rounded(float(statistics.median(values))),
                "minimum_node_score": rounded(min(values)),
                "maximum_node_score": rounded(max(values)),
            }
            for profile_name, values in profile_score_values.items()
        }
        boundary_articulation_nodes = boundary_nodes.intersection(
            articulation_points
        )
        external_articulation_attachments = (
            external_attachment_nodes.intersection(articulation_points)
        )
        boundary_bridges = boundary_edges.intersection(bridges)
        records.append(
            {
                "id": group_id,
                "label": group_label(members, nodes),
                "retained_node_count": len(members),
                "internal_edges": internal_edges,
                "density": rounded(density),
                "has_cycle": has_cycle,
                "boundary_node_count": len(boundary_nodes),
                "boundary_edge_count": len(boundary_edges),
                "neighbouring_group_count": len(neighbouring_groups),
                "boundary_ratio": rounded(boundary_ratio),
                "edge_boundary_ratio": (
                    rounded(edge_boundary_ratio)
                    if edge_boundary_ratio is not None
                    else None
                ),
                "articulation_node_count": len(
                    member_set.intersection(articulation_points)
                ),
                "boundary_articulation_node_count": len(
                    boundary_articulation_nodes
                ),
                "external_articulation_attachment_count": len(
                    external_articulation_attachments
                ),
                "boundary_bridge_count": len(boundary_bridges),
                "positive_node_counts": positive_counts,
                "positive_node_percentages": {
                    profile_name: percentage(count, len(members))
                    for profile_name, count in positive_counts.items()
                },
                "profile_score_summaries": profile_score_summaries,
                "_boundary_ratio": boundary_ratio,
                "_edge_boundary_ratio": edge_boundary_ratio,
                "_positive_node_percentages_exact": {
                    profile_name: 100.0 * count / len(members)
                    for profile_name, count in positive_counts.items()
                },
                "_profile_score_values": profile_score_values,
            }
        )

    graph_edges = count_edges(drawable_ids, adjacency)
    cross_group_edges = graph_edges - total_internal_edges
    boundary_edge_incidence_count = sum(
        record["boundary_edge_count"] for record in records
    )
    if boundary_edge_incidence_count != 2 * cross_group_edges:
        raise AssertionError(
            "Each cross-group edge must be counted by exactly two groups"
        )

    example_candidates = [
        record
        for record in records
        if record["retained_node_count"] >= 4
        and record["internal_edges"] > 0
        and record["boundary_edge_count"] > 0
    ]
    narrow_examples = sorted(
        example_candidates,
        key=lambda item: (
            item["_boundary_ratio"],
            item["_edge_boundary_ratio"],
            item["boundary_edge_count"],
            -item["retained_node_count"],
            int(item["id"]),
        ),
    )[:6]
    wide_examples = sorted(
        example_candidates,
        key=lambda item: (
            -item["_boundary_ratio"],
            -item["_edge_boundary_ratio"],
            -item["boundary_edge_count"],
            -item["retained_node_count"],
            int(item["id"]),
        ),
    )[:6]

    def example_record(record: JsonObject) -> JsonObject:
        return {
            key: value
            for key, value in record.items()
            if not key.startswith("_") and key != "positive_node_counts"
        }

    profile_summaries: list[JsonObject] = []
    for profile_name, profile_keys in REPRESENTATIVE_PROFILES.items():
        profile_summaries.append(
            {
                "name": profile_name,
                "unit_weight_keys": [
                    f"{stat_type}/{modifier_type}"
                    for stat_type, modifier_type in profile_keys
                ],
                "groups_with_any_positive_node": sum(
                    record["positive_node_counts"][profile_name] > 0
                    for record in records
                ),
                "groups_with_all_nodes_positive": sum(
                    record["positive_node_counts"][profile_name]
                    == record["retained_node_count"]
                    for record in records
                ),
                "positive_node_percentage_distribution": distribution_summary(
                    [
                        record["_positive_node_percentages_exact"][profile_name]
                        for record in records
                    ]
                ),
                "group_total_score_distribution": distribution_summary(
                    [
                        sum(record["_profile_score_values"][profile_name])
                        for record in records
                    ]
                ),
                "group_mean_node_score_distribution": distribution_summary(
                    [
                        statistics.fmean(
                            record["_profile_score_values"][profile_name]
                        )
                        for record in records
                    ]
                ),
                "group_maximum_node_score_distribution": distribution_summary(
                    [
                        max(record["_profile_score_values"][profile_name])
                        for record in records
                    ]
                ),
            }
        )

    groups_with_zero_internal_edges = sum(
        record["internal_edges"] == 0 for record in records
    )

    return {
        "definitions": {
            "boundary_node": (
                "retained node with at least one retained neighbour in a "
                "different official export group"
            ),
            "boundary_edge": (
                "retained undirected edge whose endpoints have different "
                "official export groups"
            ),
            "boundary_ratio": (
                "boundary_node_count / retained_node_count"
            ),
            "edge_boundary_ratio": (
                "boundary_edge_count / internal_edges; null when "
                "internal_edges is zero"
            ),
            "profile_score": (
                "production StatScorer score with unit weights; a node is "
                "positive exactly when score > 0; every retained group also "
                "gets total, mean, median, minimum, and maximum node-score "
                "summaries for every profile"
            ),
            "quantiles": "nearest-rank over all retained groups",
        },
        "raw_group_count": len(groups),
        "groups_with_drawable_nodes": len(by_group),
        "groups_without_drawable_nodes": len(groups) - len(by_group),
        "drawable_group_size_histogram": numeric_histogram(
            len(members) for members in by_group.values()
        ),
        "mean_induced_density_for_size_at_least_2": rounded(
            statistics.fmean(nontrivial_densities)
            if nontrivial_densities
            else 0.0
        ),
        "groups_with_induced_cycle": groups_with_cycle,
        "internal_edge_count": total_internal_edges,
        "cross_group_edge_count": cross_group_edges,
        "internal_edge_percentage": percentage(
            total_internal_edges, graph_edges
        ),
        "groups_with_zero_internal_edges": groups_with_zero_internal_edges,
        "groups_with_no_boundary_edges": sum(
            record["boundary_edge_count"] == 0 for record in records
        ),
        "groups_with_one_boundary_node": sum(
            record["boundary_node_count"] == 1 for record in records
        ),
        "groups_with_one_neighbouring_group": sum(
            record["neighbouring_group_count"] == 1 for record in records
        ),
        "distributions": {
            "retained_node_count": distribution_summary(
                [record["retained_node_count"] for record in records]
            ),
            "internal_edge_count": distribution_summary(
                [record["internal_edges"] for record in records]
            ),
            "boundary_node_count": distribution_summary(
                [record["boundary_node_count"] for record in records]
            ),
            "boundary_edge_count": distribution_summary(
                [record["boundary_edge_count"] for record in records]
            ),
            "neighbouring_group_count": distribution_summary(
                [record["neighbouring_group_count"] for record in records]
            ),
            "boundary_ratio": distribution_summary(
                [record["_boundary_ratio"] for record in records]
            ),
            "edge_boundary_ratio_defined": distribution_summary(
                [
                    record["_edge_boundary_ratio"]
                    for record in records
                    if record["_edge_boundary_ratio"] is not None
                ]
            ),
        },
        "separator_involvement": {
            "groups_containing_articulation_point": sum(
                record["articulation_node_count"] > 0 for record in records
            ),
            "groups_with_boundary_articulation_point": sum(
                record["boundary_articulation_node_count"] > 0
                for record in records
            ),
            "groups_with_external_articulation_attachment": sum(
                record["external_articulation_attachment_count"] > 0
                for record in records
            ),
            "groups_with_boundary_bridge": sum(
                record["boundary_bridge_count"] > 0 for record in records
            ),
            "cross_group_bridges": len(
                {
                    edge
                    for edge in bridges
                    if node_group[edge[0]] != node_group[edge[1]]
                }
            ),
        },
        "representative_profiles": profile_summaries,
        "example_selection": (
            "Both sets require at least four retained nodes, at least one "
            "internal edge, and at least one boundary edge. Narrow examples "
            "are the first six by ascending boundary ratio then edge-boundary "
            "ratio; wide examples reverse both ratios. Remaining tie-breaks "
            "are boundary edges, retained size, then numeric group ID."
        ),
        "narrow_boundary_examples": [
            example_record(record) for record in narrow_examples
        ],
        "wide_boundary_examples": [
            example_record(record) for record in wide_examples
        ],
        "invariants": {
            "internal_plus_cross_edges_equals_graph_edges": (
                total_internal_edges + cross_group_edges == graph_edges
            ),
            "boundary_edge_incidences_equal_twice_cross_group_edges": (
                boundary_edge_incidence_count == 2 * cross_group_edges
            ),
        },
    }


def two_core(
    node_ids: Sequence[str], adjacency: Mapping[str, set[str]]
) -> set[str]:
    remaining = set(node_ids)
    degrees = {
        node_id: len(adjacency.get(node_id, set())) for node_id in node_ids
    }
    queue = deque(
        node_id
        for node_id in node_ids
        if degrees[node_id] < 2
    )

    while queue:
        node_id = queue.popleft()
        if node_id not in remaining:
            continue
        remaining.remove(node_id)
        for neighbour in sorted_node_ids(adjacency.get(node_id, set())):
            if neighbour not in remaining:
                continue
            degrees[neighbour] -= 1
            if degrees[neighbour] == 1:
                queue.append(neighbour)

    return remaining


def triangle_metrics(
    node_ids: Sequence[str], adjacency: Mapping[str, set[str]]
) -> JsonObject:
    rank = {node_id: index for index, node_id in enumerate(node_ids)}
    triangles = 0
    for node_id in node_ids:
        for neighbour in adjacency.get(node_id, set()):
            if rank[neighbour] <= rank[node_id]:
                continue
            triangles += sum(
                1
                for third in adjacency[node_id].intersection(
                    adjacency[neighbour]
                )
                if rank[third] > rank[neighbour]
            )

    wedges = sum(
        degree * (degree - 1) // 2
        for degree in (
            len(adjacency.get(node_id, set())) for node_id in node_ids
        )
    )
    local_coefficients: list[float] = []
    for node_id in node_ids:
        neighbours = adjacency.get(node_id, set())
        degree = len(neighbours)
        if degree < 2:
            continue
        neighbour_edges_twice = sum(
            len(adjacency[neighbour].intersection(neighbours))
            for neighbour in neighbours
        )
        neighbour_edges = neighbour_edges_twice // 2
        local_coefficients.append(
            neighbour_edges / (degree * (degree - 1) / 2)
        )

    return {
        "triangle_count": triangles,
        "connected_triple_count": wedges,
        "global_transitivity": rounded(
            3.0 * triangles / wedges if wedges else 0.0
        ),
        "mean_local_clustering_degree_at_least_2": rounded(
            statistics.fmean(local_coefficients)
            if local_coefficients
            else 0.0
        ),
    }


def separator_metrics(
    node_ids: Sequence[str],
    adjacency: Mapping[str, set[str]],
    nodes: Mapping[str, JsonObject],
) -> tuple[JsonObject, set[str], set[Edge]]:
    """Find articulation points, bridges, and vertex blocks with Tarjan DFS.

    The edge stack gives the exact maximal vertex-biconnected blocks of this
    simple undirected graph.  For a total partition of graph edges and explicit
    coverage of every retained vertex, this report uses the standard block-cut
    convention that a bridge is a two-vertex/one-edge block and an isolated
    vertex is a singleton/zero-edge block.
    """

    # The passive graph can have a DFS depth larger than Python's default.
    sys.setrecursionlimit(max(sys.getrecursionlimit(), len(node_ids) * 4 + 100))
    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    subtree_size: dict[str, int] = {}
    root_for: dict[str, str] = {}
    root_child_count: Counter[str] = Counter()
    separating_child_sizes: dict[str, list[int]] = defaultdict(list)
    bridges: list[Edge] = []
    edge_stack: list[Edge] = []
    block_edges: list[tuple[Edge, ...]] = []
    isolated_block_vertices: list[str] = []
    component_sizes: dict[str, int] = {}
    clock = 0

    def visit(node_id: str, root: str) -> None:
        nonlocal clock
        clock += 1
        discovery[node_id] = clock
        low[node_id] = clock
        subtree_size[node_id] = 1
        root_for[node_id] = root

        for neighbour in sorted_node_ids(adjacency.get(node_id, set())):
            edge = canonical_edge(node_id, neighbour)
            if neighbour not in discovery:
                parent[neighbour] = node_id
                edge_stack.append(edge)
                if node_id == root:
                    root_child_count[root] += 1
                visit(neighbour, root)
                subtree_size[node_id] += subtree_size[neighbour]
                low[node_id] = min(low[node_id], low[neighbour])

                if low[neighbour] >= discovery[node_id]:
                    separating_child_sizes[node_id].append(
                        subtree_size[neighbour]
                    )
                    component_edges: list[Edge] = []
                    while edge_stack:
                        popped = edge_stack.pop()
                        component_edges.append(popped)
                        if popped == edge:
                            break
                    else:
                        raise AssertionError(
                            "Tarjan edge stack did not contain its tree edge"
                        )
                    block_edges.append(
                        tuple(sorted(component_edges, key=edge_sort_key))
                    )
                if low[neighbour] > discovery[node_id]:
                    bridges.append(edge)
            elif (
                neighbour != parent.get(node_id)
                and discovery[neighbour] < discovery[node_id]
            ):
                # Push each undirected back edge exactly once, from descendant
                # to ancestor.  Edges to already visited descendants were
                # already pushed by that descendant.
                edge_stack.append(edge)
                low[node_id] = min(low[node_id], discovery[neighbour])

    for root in node_ids:
        if root in discovery:
            continue
        parent[root] = None
        visit(root, root)
        component_sizes[root] = subtree_size[root]
        if not adjacency.get(root, set()):
            isolated_block_vertices.append(root)
        if edge_stack:
            raise AssertionError(
                "Tarjan edge stack must be empty after each connected component"
            )

    articulation_records: list[JsonObject] = []
    for node_id in node_ids:
        root = root_for[node_id]
        component_size = component_sizes[root]
        separating_sizes = separating_child_sizes.get(node_id, [])

        if parent[node_id] is None:
            if root_child_count[node_id] <= 1:
                continue
            pieces = list(separating_sizes)
        else:
            if not separating_sizes:
                continue
            remainder = component_size - 1 - sum(separating_sizes)
            pieces = [*separating_sizes]
            if remainder:
                pieces.append(remainder)

        pieces.sort(reverse=True)
        largest_piece = max(pieces, default=0)
        articulation_records.append(
            {
                "id": node_id,
                "name": node_display_name(nodes, node_id),
                "degree": len(adjacency[node_id]),
                "component_size": component_size,
                "components_after_removal": len(pieces),
                "largest_piece": largest_piece,
                "nodes_outside_largest_piece": (
                    component_size - 1 - largest_piece
                ),
                "piece_sizes": pieces,
            }
        )

    articulation_records.sort(
        key=lambda item: (
            -item["components_after_removal"],
            -item["nodes_outside_largest_piece"],
            -item["degree"],
            node_sort_key(item["id"]),
        )
    )
    bridges.sort(
        key=edge_sort_key
    )
    articulation_ids = {
        record["id"] for record in articulation_records
    }
    bridge_set = set(bridges)

    block_records: list[tuple[set[str], tuple[Edge, ...]]] = []
    for edges in block_edges:
        vertices = {endpoint for edge in edges for endpoint in edge}
        block_records.append((vertices, edges))
    block_records.extend(
        ({node_id}, tuple()) for node_id in isolated_block_vertices
    )

    # Verify the decomposition internally.  These identities are independent
    # of the report formatting and catch lost/duplicated stack edges, missing
    # isolated vertices, or disagreement between bridge and block semantics.
    graph_edges = {
        canonical_edge(node_id, neighbour)
        for node_id in node_ids
        for neighbour in adjacency.get(node_id, set())
    }
    edge_memberships: Counter[Edge] = Counter(
        edge for _, edges in block_records for edge in edges
    )
    if set(edge_memberships) != graph_edges or any(
        count != 1 for count in edge_memberships.values()
    ):
        raise AssertionError(
            "Every graph edge must occur in exactly one vertex block"
        )

    singleton_vertices = {
        next(iter(vertices))
        for vertices, edges in block_records
        if len(vertices) == 1 and not edges
    }
    expected_isolated = {
        node_id for node_id in node_ids if not adjacency.get(node_id, set())
    }
    if singleton_vertices != expected_isolated:
        raise AssertionError(
            "Singleton blocks must correspond exactly to isolated vertices"
        )

    one_edge_blocks = {
        edges[0]
        for vertices, edges in block_records
        if len(vertices) == 2 and len(edges) == 1
    }
    if one_edge_blocks != bridge_set:
        raise AssertionError(
            "Two-vertex/one-edge blocks must correspond exactly to bridges"
        )

    block_memberships: Counter[str] = Counter(
        vertex for vertices, _ in block_records for vertex in vertices
    )
    articulation_from_blocks = {
        node_id for node_id, count in block_memberships.items() if count > 1
    }
    if articulation_from_blocks != articulation_ids:
        raise AssertionError(
            "Vertices shared by blocks must be exactly the articulation points"
        )

    block_vertex_sizes = [len(vertices) for vertices, _ in block_records]
    block_edge_sizes = [len(edges) for _, edges in block_records]
    cyclic_block_count = sum(
        len(edges) >= len(vertices)
        for vertices, edges in block_records
        if len(vertices) >= 3
    )
    report = {
        "algorithm": (
            "Tarjan undirected DFS with an edge stack; bridge edges are "
            "reported as two-vertex blocks and isolated vertices as "
            "singleton zero-edge blocks."
        ),
        "articulation_point_count": len(articulation_records),
        "bridge_count": len(bridges),
        "top_articulation_points": articulation_records[:12],
        "vertex_biconnected_component_count": len(block_records),
        "vertex_biconnected_component_size_histogram": numeric_histogram(
            block_vertex_sizes
        ),
        "vertex_biconnected_component_edge_count_histogram": numeric_histogram(
            block_edge_sizes
        ),
        "largest_vertex_biconnected_component_sizes": sorted(
            block_vertex_sizes, reverse=True
        )[:12],
        "maximum_vertex_biconnected_component_size": max(
            block_vertex_sizes, default=0
        ),
        "isolated_singleton_block_count": len(singleton_vertices),
        "bridge_block_count": len(one_edge_blocks),
        "blocks_with_at_least_three_vertices": sum(
            size >= 3 for size in block_vertex_sizes
        ),
        "cyclic_block_count": cyclic_block_count,
        "vertex_memberships_across_blocks": sum(block_vertex_sizes),
        "verification": {
            "every_edge_in_exactly_one_block": True,
            "singleton_blocks_equal_isolated_vertices": True,
            "two_vertex_one_edge_blocks_equal_bridges": True,
            "vertices_in_multiple_blocks_equal_articulation_points": True,
        },
    }
    return report, articulation_ids, bridge_set


def structural_metrics(
    data: JsonObject,
    drawable_ids: Sequence[str],
    adjacency: Mapping[str, set[str]],
    components: Sequence[Sequence[str]],
    separators: JsonObject,
) -> JsonObject:
    edge_count = count_edges(drawable_ids, adjacency)
    core_nodes = two_core(drawable_ids, adjacency)
    core_edges = count_edges(
        sorted_node_ids(core_nodes),
        {
            node_id: adjacency[node_id].intersection(core_nodes)
            for node_id in core_nodes
        },
    )
    return {
        "cycle_rank": edge_count - len(drawable_ids) + len(components),
        "two_core_node_count": len(core_nodes),
        "two_core_node_percentage": percentage(
            len(core_nodes), len(drawable_ids)
        ),
        "two_core_edge_count": core_edges,
        **triangle_metrics(drawable_ids, adjacency),
        **separators,
    }


def distance_buckets(distances: Iterable[int]) -> dict[str, int]:
    counts = {label: 0 for label, _, _ in DISTANCE_BUCKETS}
    for distance in distances:
        for label, lower, upper in DISTANCE_BUCKETS:
            if distance >= lower and (upper is None or distance <= upper):
                counts[label] += 1
                break
    return counts


def nearest_rank(values: Sequence[int], proportion: float) -> int:
    if not values:
        return 0
    index = max(0, math.ceil(proportion * len(values)) - 1)
    return values[index]


def create_production_services(
    tree_path: Path,
    production_adj: dict[str, list[str]],
) -> tuple[NodeLookup, PathFinder, StatParser, StatScorer, list[str]]:
    captured = io.StringIO()
    with redirect_stdout(captured):
        lookup = NodeLookup(tree_path)
    lookup_messages = [
        line.strip()
        for line in captured.getvalue().splitlines()
        if line.strip()
    ]
    pathfinder = PathFinder(production_adj, lookup)
    parser = StatParser()
    scorer = StatScorer(parser, lookup)
    return lookup, pathfinder, parser, scorer, lookup_messages


def class_start_metrics(
    data: JsonObject,
    drawable_ids: Sequence[str],
    adjacency: Mapping[str, set[str]],
    pathfinder: PathFinder,
) -> JsonObject:
    nodes: dict[str, JsonObject] = data["nodes"]
    class_definitions: list[JsonObject] = data.get("classes", [])
    starts = [
        node_id
        for node_id in drawable_ids
        if nodes[node_id].get("classStartIndex") is not None
    ]
    starts.sort(
        key=lambda node_id: (
            int(nodes[node_id]["classStartIndex"]),
            node_sort_key(node_id),
        )
    )
    records: list[JsonObject] = []

    for node_id in starts:
        class_index = int(nodes[node_id]["classStartIndex"])
        class_name = (
            str(class_definitions[class_index].get("name"))
            if class_index < len(class_definitions)
            else "<unknown>"
        )
        traversable_when_unallocated = pathfinder._is_traversable(
            node_id, allocated=set()
        )
        traversable_when_allocated = pathfinder._is_traversable(
            node_id, allocated={node_id}
        )
        distance_map, _ = pathfinder.shortest_paths_from_allocated(
            {node_id}
        )
        distance_values = sorted(distance_map.values())
        records.append(
            {
                "class_index": class_index,
                "class": class_name,
                "id": node_id,
                "export_name": node_display_name(nodes, node_id),
                "degree": len(adjacency[node_id]),
                "traversable_when_unallocated": traversable_when_unallocated,
                "traversable_when_allocated": traversable_when_allocated,
                "reachable_drawable_nodes": len(
                    set(distance_map).intersection(drawable_ids)
                ),
                "unreachable_drawable_nodes": len(drawable_ids)
                - len(set(distance_map).intersection(drawable_ids)),
                "mean_distance": rounded(
                    statistics.fmean(distance_values)
                    if distance_values
                    else 0.0,
                    2,
                ),
                "median_distance": rounded(
                    float(statistics.median(distance_values))
                    if distance_values
                    else 0.0,
                    2,
                ),
                "p90_distance_nearest_rank": nearest_rank(
                    distance_values, 0.90
                ),
                "maximum_distance": max(distance_values, default=0),
                "distance_buckets": distance_buckets(distance_values),
            }
        )

    return {
        "count": len(starts),
        "production_rule_verified_for_every_start": all(
            not record["traversable_when_unallocated"]
            and record["traversable_when_allocated"]
            for record in records
        ),
        "distance_definition": (
            "Production multi-source BFS with only the named class start "
            "allocated; other class starts are not traversable."
        ),
        "distance_bucket_order": [
            label for label, _, _ in DISTANCE_BUCKETS
        ],
        "starts": records,
    }


def stat_lines_for_nodes(
    nodes: Mapping[str, JsonObject], node_ids: Iterable[str]
) -> list[str]:
    return [
        str(raw_stat)
        for node_id in node_ids
        for raw_stat in nodes[node_id].get("stats", [])
    ]


def normalise_unparsed_pattern(raw_stat: str) -> str:
    normalised = WHITESPACE_RE.sub(" ", raw_stat.strip().lower())
    return PATTERN_NUMBER_RE.sub("{n}", normalised)


def parser_coverage(
    stat_lines: Sequence[str], parser: StatParser
) -> JsonObject:
    unique_lines = sorted(set(stat_lines), key=lambda line: (line.lower(), line))
    parsed_instances = sum(
        parser.parse(raw_stat) is not None for raw_stat in stat_lines
    )
    parsed_unique = sum(
        parser.parse(raw_stat) is not None for raw_stat in unique_lines
    )
    return {
        "stat_line_instances": len(stat_lines),
        "unique_raw_stat_lines": len(unique_lines),
        "parsed_instances": parsed_instances,
        "parsed_instance_percentage": percentage(
            parsed_instances, len(stat_lines)
        ),
        "parsed_unique_lines": parsed_unique,
        "parsed_unique_percentage": percentage(
            parsed_unique, len(unique_lines)
        ),
    }


def common_unparsed_patterns(
    stat_lines: Sequence[str], parser: StatParser, limit: int = 15
) -> tuple[list[JsonObject], JsonObject]:
    pattern_counts: Counter[str] = Counter()
    pattern_lines: dict[str, set[str]] = defaultdict(set)
    reason_counts: Counter[str] = Counter()

    for raw_stat in stat_lines:
        if parser.parse(raw_stat) is not None:
            continue
        pattern = normalise_unparsed_pattern(raw_stat)
        pattern_counts[pattern] += 1
        pattern_lines[pattern].add(raw_stat)
        reason = (
            "no_numeric_value"
            if NUMBER_RE.search(raw_stat.lower()) is None
            else "contains_numeric_value_but_unparsed"
        )
        reason_counts[reason] += 1

    ordered = sorted(
        pattern_counts,
        key=lambda pattern: (
            -pattern_counts[pattern],
            -len(pattern_lines[pattern]),
            pattern,
        ),
    )[:limit]
    records = [
        {
            "pattern": pattern,
            "instances": pattern_counts[pattern],
            "unique_lines": len(pattern_lines[pattern]),
            "example": min(
                pattern_lines[pattern],
                key=lambda line: (line.lower(), line),
            ),
        }
        for pattern in ordered
    ]
    return records, {
        reason: reason_counts[reason] for reason in sorted(reason_counts)
    }


def score_sparsity(
    data: JsonObject,
    drawable_ids: Sequence[str],
    scorer: StatScorer,
) -> JsonObject:
    nodes: dict[str, JsonObject] = data["nodes"]
    profiles: list[JsonObject] = []

    for profile_name, profile_keys in REPRESENTATIVE_PROFILES.items():
        weights = {key: 1.0 for key in profile_keys}
        scores = {
            node_id: scorer.score_node(node_id, weights)
            for node_id in drawable_ids
        }
        positive = [score for score in scores.values() if score > 0]
        negative = [score for score in scores.values() if score < 0]
        nonzero_ids = [
            node_id for node_id, score in scores.items() if score != 0
        ]
        top_ids = sorted(
            nonzero_ids,
            key=lambda node_id: (
                -scores[node_id],
                node_sort_key(node_id),
            ),
        )[:5]
        profiles.append(
            {
                "name": profile_name,
                "unit_weight_keys": [
                    f"{stat_type}/{modifier_type}"
                    for stat_type, modifier_type in profile_keys
                ],
                "nonzero_nodes": len(nonzero_ids),
                "nonzero_percentage": percentage(
                    len(nonzero_ids), len(drawable_ids)
                ),
                "zero_nodes": len(drawable_ids) - len(nonzero_ids),
                "zero_percentage": percentage(
                    len(drawable_ids) - len(nonzero_ids),
                    len(drawable_ids),
                ),
                "positive_nodes": len(positive),
                "negative_nodes": len(negative),
                "mean_score_over_all_nodes": rounded(
                    statistics.fmean(scores.values()) if scores else 0.0
                ),
                "median_nonzero_score": rounded(
                    float(
                        statistics.median(
                            score
                            for score in scores.values()
                            if score != 0
                        )
                    )
                    if nonzero_ids
                    else 0.0
                ),
                "maximum_score": rounded(max(scores.values(), default=0.0)),
                "top_nodes": [
                    {
                        "id": node_id,
                        "name": node_display_name(nodes, node_id),
                        "score": rounded(scores[node_id]),
                    }
                    for node_id in top_ids
                ],
            }
        )

    return {
        "semantics": (
            "StatScorer.score_node with weight 1.0 for every listed "
            "(stat_type, modifier_type) key; no nonlinear adjustment."
        ),
        "profiles": profiles,
    }


def stat_metrics(
    data: JsonObject,
    drawable_ids: Sequence[str],
    parser: StatParser,
    scorer: StatScorer,
) -> JsonObject:
    nodes: dict[str, JsonObject] = data["nodes"]
    raw_ids = sorted_node_ids(nodes)
    raw_lines = stat_lines_for_nodes(nodes, raw_ids)
    drawable_lines = stat_lines_for_nodes(nodes, drawable_ids)
    common_patterns, reason_counts = common_unparsed_patterns(
        drawable_lines, parser
    )

    return {
        "raw_nodes_with_no_stats": sum(
            not nodes[node_id].get("stats") for node_id in raw_ids
        ),
        "drawable_nodes_with_no_stats": sum(
            not nodes[node_id].get("stats") for node_id in drawable_ids
        ),
        "raw_export_coverage": parser_coverage(raw_lines, parser),
        "drawable_coverage": parser_coverage(drawable_lines, parser),
        "drawable_unparsed_reason_instances": reason_counts,
        "common_drawable_unparsed_patterns": common_patterns,
        "score_sparsity": score_sparsity(
            data, drawable_ids, scorer
        ),
    }


def analyse(tree_path: Path) -> JsonObject:
    tree_path = tree_path.resolve()
    data = load_tree(tree_path)
    drawable_ids, adjacency, production_adj, validation = (
        build_filtered_graph(data, tree_path)
    )
    graph, components = graph_metrics(
        data,
        drawable_ids,
        adjacency,
        production_adj,
        validation,
    )
    lookup, pathfinder, parser, scorer, lookup_messages = (
        create_production_services(tree_path, production_adj)
    )
    # Retain the production object so its construction cannot be optimised away
    # and to make the service tuple's intent explicit to type checkers/readers.
    del lookup
    separator_report, articulation_points, bridges = separator_metrics(
        drawable_ids, adjacency, data["nodes"]
    )
    structure = structural_metrics(
        data,
        drawable_ids,
        adjacency,
        components,
        separator_report,
    )

    try:
        source_path = tree_path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        source_path = str(tree_path)

    return {
        "analysis_version": 2,
        "source": {
            "path": source_path,
            "sha256": hashlib.sha256(tree_path.read_bytes()).hexdigest(),
        },
        "semantics": {
            "drawable_filter": (
                "Production poe_pathing.graph.build.is_drawable_node"
            ),
            "graph": (
                "Production load_adj, treated as undirected; drawable nodes "
                "omitted as adjacency keys are included with degree zero."
            ),
            "traversal": (
                "Production PathFinder.shortest_paths_from_allocated and "
                "_is_traversable."
            ),
            "parsing": "Production StatParser.parse.",
            "scoring": "Production StatScorer.score_node.",
            "lookup_messages": lookup_messages,
        },
        "schema": schema_metrics(data),
        "graph": graph,
        "node_types": type_metrics(data, drawable_ids),
        "groups": group_metrics(
            data,
            drawable_ids,
            adjacency,
            scorer,
            articulation_points,
            bridges,
        ),
        "class_starts": class_start_metrics(
            data, drawable_ids, adjacency, pathfinder
        ),
        "stats": stat_metrics(
            data, drawable_ids, parser, scorer
        ),
        "structure": structure,
    }


def markdown_escape(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def histogram_text(histogram: Mapping[str, int]) -> str:
    return ", ".join(
        f"{key}: {value}"
        for key, value in sorted(
            histogram.items(), key=lambda item: int(item[0])
        )
    )


def render_markdown(report: JsonObject) -> str:
    source = report["source"]
    semantics = report["semantics"]
    schema = report["schema"]
    graph = report["graph"]
    node_types = report["node_types"]
    groups = report["groups"]
    class_starts = report["class_starts"]
    stats = report["stats"]
    structure = report["structure"]
    lines: list[str] = [
        "# Passive-tree data analysis",
        "",
        (
            "Deterministic snapshot generated by "
            "`scripts/analyse_tree_data.py` from "
            f"`{source['path']}` (SHA-256 `{source['sha256']}`)."
        ),
        "",
        "Regenerate from the repository root with:",
        "",
        "```text",
        "py scripts/analyse_tree_data.py",
        "```",
        "",
        "The script also supports machine-readable output with "
        "`--format json`. No timestamp is included, so unchanged input and "
        "code produce byte-for-byte stable output.",
        "",
        "## Scope and definitions",
        "",
        "- **Measured fact:** drawable nodes use the production "
        "`is_drawable_node` predicate. Graph edges use production `load_adj`.",
        "- Drawable nodes missing from the production adjacency mapping are "
        "included here as degree-zero vertices, so retained-node and component "
        "counts are not silently understated.",
        "- Parser coverage is reported both by line occurrence and by unique "
        "case-sensitive raw line. Score profiles use production `StatScorer` "
        "with unit weights and its current linear/additive semantics.",
        "- `normal` means none of the other reported special categories. "
        "Special categories can overlap. Jewel-related and ascendancy "
        f"definitions are: {node_types['definitions']['jewel_related']}; "
        f"{node_types['definitions']['ascendancy']}.",
        "- Group density is induced undirected edges divided by all possible "
        "edges among the drawable members of an export group. Export groups "
        "are metadata clusters, not assumed graph components.",
        "- Production `NodeLookup` emitted during loading: `"
        + ("; ".join(semantics["lookup_messages"]) or "<none>")
        + "`. This is retained as a data-quality caveat.",
        "",
        "## Export schema",
        "",
        f"- Top-level keys ({len(schema['top_level_keys'])}): "
        f"`{', '.join(schema['top_level_keys'])}`.",
        f"- Raw nodes: **{schema['raw_node_count']}**; raw groups: "
        f"**{schema['raw_group_count']}**.",
        "- Graph construction reads `nodes`, `groups`, each node's `group`, "
        "`in`, and `out`; filtering additionally reads ascendancy, mastery, "
        "proxy, and name fields.",
        f"- Node field union: `{', '.join(schema['node_field_union'])}`.",
        f"- Group field union: `{', '.join(schema['group_field_union'])}`.",
        "",
        "Relevant node-field presence:",
        "",
        "| Field | Nodes containing field |",
        "|---|---:|",
    ]
    for field, count in schema["relevant_node_field_presence"].items():
        lines.append(f"| `{field}` | {count} |")

    lines.extend(
        [
            "",
            "## Filtered graph",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Raw nodes | {schema['raw_node_count']} |",
            f"| Nodes retained by `is_drawable_node` | "
            f"{graph['drawable_node_count']} |",
            f"| Keys emitted by production `load_adj` | "
            f"{graph['production_adjacency_key_count']} |",
            f"| Drawable nodes absent as adjacency keys | "
            f"{graph['drawable_nodes_absent_from_adjacency']} |",
            f"| Isolated drawable nodes | "
            f"{graph['isolated_drawable_node_count']} |",
            f"| Undirected edges | {graph['undirected_edge_count']} |",
            f"| Connected components | {graph['component_count']} |",
            f"| Average degree | {graph['average_degree']} |",
            f"| Maximum degree | {graph['maximum_degree']} |",
            f"| Self-loops | {graph['self_loop_count']} |",
            f"| Asymmetric adjacency pairs | "
            f"{graph['asymmetric_pair_count']} |",
            "",
            "Component-size histogram (`size: component count`): "
            f"`{histogram_text(graph['component_size_histogram'])}`.",
            "",
            "Degree histogram (`degree: node count`): "
            f"`{histogram_text(graph['degree_histogram'])}`.",
            "",
            "Highest-degree nodes (a deterministic top 12):",
            "",
            "| ID | Name | Group | Degree |",
            "|---:|---|---:|---:|",
        ]
    )
    for node in graph["high_degree_nodes"]:
        lines.append(
            f"| {node['id']} | {markdown_escape(node['name'])} | "
            f"{node['group']} | {node['degree']} |"
        )

    if graph["isolated_drawable_nodes"]:
        isolated_text = ", ".join(
            f"{item['id']} ({markdown_escape(item['name'])})"
            for item in graph["isolated_drawable_nodes"]
        )
        lines.extend(
            [
                "",
                f"Isolated drawable nodes: {isolated_text}.",
            ]
        )

    lines.extend(
        [
            "",
            "## Node kinds",
            "",
            "Counts use the explicit definitions above; special rows are not "
            "mutually exclusive.",
            "",
            "| Kind | Raw nodes | Drawable nodes |",
            "|---|---:|---:|",
        ]
    )
    for category, raw_count in node_types["raw"].items():
        lines.append(
            f"| `{category}` | {raw_count} | "
            f"{node_types['drawable'][category]} |"
        )

    lines.extend(
        [
            "",
            "## Export groups / local clusters",
            "",
            "An official export **group** is analysed only over retained "
            "nodes. A **boundary node** has a retained neighbour in another "
            "group, and a **boundary edge** joins retained nodes in different "
            "groups. `boundary ratio = boundary nodes / retained nodes`; "
            "`edge boundary ratio = boundary edges / internal edges`. The "
            "edge ratio is undefined (JSON `null`) for groups with zero "
            "internal edges rather than being reported as zero.",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Raw export groups | {groups['raw_group_count']} |",
            f"| Groups with drawable nodes | "
            f"{groups['groups_with_drawable_nodes']} |",
            f"| Groups empty after filtering | "
            f"{groups['groups_without_drawable_nodes']} |",
            f"| Within-group edges | {groups['internal_edge_count']} |",
            f"| Cross-group edges | {groups['cross_group_edge_count']} |",
            f"| Within-group edge share | "
            f"{groups['internal_edge_percentage']}% |",
            f"| Mean induced density, groups of size >= 2 | "
            f"{groups['mean_induced_density_for_size_at_least_2']} |",
            f"| Groups whose drawable induced subgraph has a cycle | "
            f"{groups['groups_with_induced_cycle']} |",
            f"| Groups with zero internal edges / undefined edge ratio | "
            f"{groups['groups_with_zero_internal_edges']} |",
            f"| Groups with no boundary edge | "
            f"{groups['groups_with_no_boundary_edges']} |",
            f"| Groups with exactly one boundary node | "
            f"{groups['groups_with_one_boundary_node']} |",
            f"| Groups with exactly one neighbouring group | "
            f"{groups['groups_with_one_neighbouring_group']} |",
            "",
            "Drawable group-size histogram (`size: group count`): "
            f"`{histogram_text(groups['drawable_group_size_histogram'])}`.",
            "",
            "Boundary distributions (nearest-rank quantiles over retained "
            "groups):",
            "",
            "| Measure | N | Min | P25 | Median | P75 | P90 | Max | Mean |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    distribution_labels = {
        "retained_node_count": "Retained nodes",
        "internal_edge_count": "Internal edges",
        "boundary_node_count": "Boundary nodes",
        "boundary_edge_count": "Boundary edges",
        "neighbouring_group_count": "Neighbouring groups",
        "boundary_ratio": "Boundary ratio",
        "edge_boundary_ratio_defined": "Edge boundary ratio (defined only)",
    }
    for key, label in distribution_labels.items():
        distribution = groups["distributions"][key]
        lines.append(
            f"| {label} | {distribution['count']} | "
            f"{distribution['minimum']} | "
            f"{distribution['p25_nearest_rank']} | "
            f"{distribution['median_nearest_rank']} | "
            f"{distribution['p75_nearest_rank']} | "
            f"{distribution['p90_nearest_rank']} | "
            f"{distribution['maximum']} | {distribution['mean']} |"
        )

    separator_groups = groups["separator_involvement"]
    lines.extend(
        [
            "",
            "Group involvement with exact graph separators:",
            "",
            "| Measure | Groups or edges |",
            "|---|---:|",
            f"| Groups containing an articulation point | "
            f"{separator_groups['groups_containing_articulation_point']} |",
            f"| Groups with an articulation point on their boundary | "
            f"{separator_groups['groups_with_boundary_articulation_point']} |",
            f"| Groups whose external attachment includes an articulation "
            f"point | {separator_groups['groups_with_external_articulation_attachment']} |",
            f"| Groups incident to a boundary bridge | "
            f"{separator_groups['groups_with_boundary_bridge']} |",
            f"| Bridges whose endpoints are in different groups | "
            f"{separator_groups['cross_group_bridges']} |",
            "",
            "Positive-node percentages by representative production-scoring "
            "profile. Percentages are calculated within every retained group; "
            "the exact unit-weight keys are listed in the score-sparsity "
            "section below.",
            "",
            "| Profile | Groups with any positive | Fully positive groups | "
            "Min | P25 | Median | P75 | P90 | Max | Mean |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for profile in groups["representative_profiles"]:
        distribution = profile["positive_node_percentage_distribution"]
        lines.append(
            f"| `{profile['name']}` | "
            f"{profile['groups_with_any_positive_node']} | "
            f"{profile['groups_with_all_nodes_positive']} | "
            f"{distribution['minimum']}% | "
            f"{distribution['p25_nearest_rank']}% | "
            f"{distribution['median_nearest_rank']}% | "
            f"{distribution['p75_nearest_rank']}% | "
            f"{distribution['p90_nearest_rank']}% | "
            f"{distribution['maximum']}% | {distribution['mean']}% |"
        )

    lines.extend(
        [
            "",
            "Per-group node-value summaries use the same production scores. "
            "Every retained group receives total, mean, median, minimum, and "
            "maximum node scores for every profile; this compact table reports "
            "the aggregate distributions of group totals and group means.",
            "",
            "| Profile | Measure across groups | Min | P25 | Median | P75 | "
            "P90 | Max | Mean |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for profile in groups["representative_profiles"]:
        for key, label in (
            ("group_total_score_distribution", "Group total score"),
            ("group_mean_node_score_distribution", "Group mean node score"),
        ):
            distribution = profile[key]
            lines.append(
                f"| `{profile['name']}` | {label} | "
                f"{distribution['minimum']} | "
                f"{distribution['p25_nearest_rank']} | "
                f"{distribution['median_nearest_rank']} | "
                f"{distribution['p75_nearest_rank']} | "
                f"{distribution['p90_nearest_rank']} | "
                f"{distribution['maximum']} | {distribution['mean']} |"
            )

    profile_order = [
        profile["name"] for profile in groups["representative_profiles"]
    ]

    def append_group_examples(title: str, key: str) -> None:
        lines.extend(
            [
                "",
                title,
                "",
                "| Group | Label | Nodes | Internal | Boundary nodes/edges | "
                "Neighbours | Ratios (node/edge) | Articulation "
                "(all/boundary/external) | Boundary bridges | Positive % "
                f"({'/'.join(profile_order)}) | Total score "
                f"({'/'.join(profile_order)}) |",
                "|---:|---|---:|---:|---:|---:|---|---|---:|---|---|",
            ]
        )
        for group in groups[key]:
            positive_text = "/".join(
                str(group["positive_node_percentages"][profile_name])
                for profile_name in profile_order
            )
            score_total_text = "/".join(
                str(
                    group["profile_score_summaries"][profile_name][
                        "total_score"
                    ]
                )
                for profile_name in profile_order
            )
            lines.append(
                f"| {group['id']} | {markdown_escape(group['label'])} | "
                f"{group['retained_node_count']} | {group['internal_edges']} | "
                f"{group['boundary_node_count']}/{group['boundary_edge_count']} | "
                f"{group['neighbouring_group_count']} | "
                f"{group['boundary_ratio']}/{group['edge_boundary_ratio']} | "
                f"{group['articulation_node_count']}/"
                f"{group['boundary_articulation_node_count']}/"
                f"{group['external_articulation_attachment_count']} | "
                f"{group['boundary_bridge_count']} | {positive_text} | "
                f"{score_total_text} |"
            )

    lines.extend(["", groups["example_selection"]])
    append_group_examples(
        "Narrow-boundary examples under that deterministic selection:",
        "narrow_boundary_examples",
    )
    append_group_examples(
        "Wide-boundary examples under that deterministic selection:",
        "wide_boundary_examples",
    )

    lines.extend(
        [
            "",
            "## Class starts and shortest-path distances",
            "",
            f"The export has **{class_starts['count']}** retained class starts. "
            "The production traversal-rule check is "
            f"**{'PASS' if class_starts['production_rule_verified_for_every_start'] else 'FAIL'}**: "
            "every start is blocked when unallocated and traversable when it "
            "is in the allocated set.",
            "",
            f"Distance method: {class_starts['distance_definition']}",
            "",
            "| Class | Index | Node ID | Export name | Degree | Reachable | "
            "Unreachable | Mean | Median | P90 | Max |",
            "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for start in class_starts["starts"]:
        lines.append(
            f"| {markdown_escape(start['class'])} | "
            f"{start['class_index']} | {start['id']} | "
            f"{markdown_escape(start['export_name'])} | {start['degree']} | "
            f"{start['reachable_drawable_nodes']} | "
            f"{start['unreachable_drawable_nodes']} | "
            f"{start['mean_distance']} | {start['median_distance']} | "
            f"{start['p90_distance_nearest_rank']} | "
            f"{start['maximum_distance']} |"
        )

    lines.extend(
        [
            "",
            "Distance bucket counts (`0`, `1-5`, `6-10`, `11-15`, `16-20`, "
            "`21-30`, `31-40`, `41-50`, `51-60`, `61+`):",
            "",
            "| Class | Counts in bucket order |",
            "|---|---|",
        ]
    )
    for start in class_starts["starts"]:
        counts = ", ".join(
            str(start["distance_buckets"][label])
            for label in class_starts["distance_bucket_order"]
        )
        lines.append(f"| {markdown_escape(start['class'])} | `{counts}` |")

    raw_coverage = stats["raw_export_coverage"]
    drawable_coverage = stats["drawable_coverage"]
    lines.extend(
        [
            "",
            "## Stats and current parser coverage",
            "",
            "| Metric | Raw export | Drawable nodes |",
            "|---|---:|---:|",
            f"| Nodes with no stats | {stats['raw_nodes_with_no_stats']} | "
            f"{stats['drawable_nodes_with_no_stats']} |",
            f"| Stat-line occurrences | "
            f"{raw_coverage['stat_line_instances']} | "
            f"{drawable_coverage['stat_line_instances']} |",
            f"| Unique raw stat lines | "
            f"{raw_coverage['unique_raw_stat_lines']} | "
            f"{drawable_coverage['unique_raw_stat_lines']} |",
            f"| Parsed occurrences | {raw_coverage['parsed_instances']} "
            f"({raw_coverage['parsed_instance_percentage']}%) | "
            f"{drawable_coverage['parsed_instances']} "
            f"({drawable_coverage['parsed_instance_percentage']}%) |",
            f"| Parsed unique lines | {raw_coverage['parsed_unique_lines']} "
            f"({raw_coverage['parsed_unique_percentage']}%) | "
            f"{drawable_coverage['parsed_unique_lines']} "
            f"({drawable_coverage['parsed_unique_percentage']}%) |",
            "",
            "Coarse unparsed drawable occurrence classifications: "
            + ", ".join(
                f"`{reason}` = {count}"
                for reason, count in stats[
                    "drawable_unparsed_reason_instances"
                ].items()
            )
            + ".",
            "",
            "Most common normalised unparsed patterns (numbers become `{n}`):",
            "",
            "| Pattern | Occurrences | Unique lines | Example |",
            "|---|---:|---:|---|",
        ]
    )
    for pattern in stats["common_drawable_unparsed_patterns"]:
        lines.append(
            f"| `{markdown_escape(pattern['pattern'])}` | "
            f"{pattern['instances']} | {pattern['unique_lines']} | "
            f"{markdown_escape(pattern['example'])} |"
        )

    lines.extend(
        [
            "",
            "## Representative score sparsity",
            "",
            f"{stats['score_sparsity']['semantics']}",
            "",
        ]
    )
    for profile in stats["score_sparsity"]["profiles"]:
        lines.append(
            f"- `{profile['name']}` keys: "
            f"`{', '.join(profile['unit_weight_keys'])}`."
        )
    lines.extend(
        [
            "",
            "| Profile | Nonzero nodes | Zero nodes | Positive | Negative | "
            "Mean/all | Median nonzero | Max |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for profile in stats["score_sparsity"]["profiles"]:
        lines.append(
            f"| `{profile['name']}` | {profile['nonzero_nodes']} "
            f"({profile['nonzero_percentage']}%) | {profile['zero_nodes']} "
            f"({profile['zero_percentage']}%) | {profile['positive_nodes']} | "
            f"{profile['negative_nodes']} | "
            f"{profile['mean_score_over_all_nodes']} | "
            f"{profile['median_nonzero_score']} | "
            f"{profile['maximum_score']} |"
        )

    lines.extend(
        [
            "",
            "Top five nonzero nodes per profile under these exact unit weights:",
            "",
        ]
    )
    for profile in stats["score_sparsity"]["profiles"]:
        top_text = "; ".join(
            f"{node['id']} {markdown_escape(node['name'])} ({node['score']})"
            for node in profile["top_nodes"]
        )
        lines.append(f"- `{profile['name']}`: {top_text}.")

    lines.extend(
        [
            "",
            "## Cycles, separators, hubs, and local density",
            "",
            "| Structural metric | Value |",
            "|---|---:|",
            f"| Cycle rank (`E - V + components`) | "
            f"{structure['cycle_rank']} |",
            f"| Nodes in the graph's 2-core | "
            f"{structure['two_core_node_count']} "
            f"({structure['two_core_node_percentage']}%) |",
            f"| Edges in the 2-core | {structure['two_core_edge_count']} |",
            f"| Triangles | {structure['triangle_count']} |",
            f"| Connected triples | "
            f"{structure['connected_triple_count']} |",
            f"| Global transitivity | "
            f"{structure['global_transitivity']} |",
            f"| Mean local clustering (degree >= 2) | "
            f"{structure['mean_local_clustering_degree_at_least_2']} |",
            f"| One-node separators (articulation points) | "
            f"{structure['articulation_point_count']} |",
            f"| Bridges (one-edge separators) | "
            f"{structure['bridge_count']} |",
            f"| Vertex-biconnected blocks (including trivial blocks) | "
            f"{structure['vertex_biconnected_component_count']} |",
            f"| Isolated-vertex singleton blocks | "
            f"{structure['isolated_singleton_block_count']} |",
            f"| Bridge two-vertex blocks | "
            f"{structure['bridge_block_count']} |",
            f"| Blocks with at least three vertices | "
            f"{structure['blocks_with_at_least_three_vertices']} |",
            f"| Cyclic vertex blocks | {structure['cyclic_block_count']} |",
            f"| Maximum vertex-block size | "
            f"{structure['maximum_vertex_biconnected_component_size']} |",
            "",
            f"Vertex-block convention and algorithm: {structure['algorithm']}",
            "",
            "Vertex-block size histogram (`vertices: block count`): "
            f"`{histogram_text(structure['vertex_biconnected_component_size_histogram'])}`.",
            "",
            "Vertex-block edge-count histogram (`edges: block count`): "
            f"`{histogram_text(structure['vertex_biconnected_component_edge_count_histogram'])}`.",
            "",
            "Largest vertex-block sizes: `"
            + ", ".join(
                str(size)
                for size in structure[
                    "largest_vertex_biconnected_component_sizes"
                ]
            )
            + "`.",
            "",
            "Tarjan decomposition invariants: **"
            + (
                "PASS"
                if all(structure["verification"].values())
                else "FAIL"
            )
            + "**. Every edge occurs in exactly one block; singleton blocks "
            "equal isolated vertices; two-vertex/one-edge blocks equal "
            "bridges; and vertices shared by multiple blocks equal the "
            "articulation-point set.",
            "",
            "Articulation points ranked by number of resulting pieces, then "
            "nodes separated from the largest remaining piece:",
            "",
            "| ID | Name | Degree | Original component | Pieces | "
            "Outside largest piece | Piece sizes |",
            "|---:|---|---:|---:|---:|---:|---|",
        ]
    )
    for node in structure["top_articulation_points"]:
        lines.append(
            f"| {node['id']} | {markdown_escape(node['name'])} | "
            f"{node['degree']} | {node['component_size']} | "
            f"{node['components_after_removal']} | "
            f"{node['nodes_outside_largest_piece']} | "
            f"`{', '.join(str(size) for size in node['piece_sizes'])}` |"
        )

    lines.extend(
        [
            "",
            "### Evidence-based implications",
            "",
            "- **Measured:** the degree table identifies hubs; articulation "
            "points, bridges, and vertex-biconnected blocks are exact for this "
            "filtered undirected graph; cycle rank, 2-core, triangles, group "
            "density, and group boundaries quantify cyclic local structure.",
            "- **Measured:** official groups often have narrow boundaries, but "
            "the boundary tables also show wide groups and profile-dependent "
            "value sparsity. These descriptive ratios do not establish a safe "
            "graph contraction.",
            "- **Hypothesis:** articulation points, bridges, and export-group "
            "boundaries may support safe decomposition or cluster-level "
            "preprocessing, but only if the optimisation state preserves "
            "visited-set and class-start traversal constraints.",
            "- **Hypothesis:** cycles and a large 2-core create many alternate "
            "simple paths and make a state signature based only on current node "
            "and cost less informative. These data alone do not prove a "
            "particular optimiser failure.",
            "- **Caveat:** start-specific traversable graphs exclude the six "
            "unallocated class starts. The separator metrics above describe "
            "the common drawable graph before that per-query exclusion.",
            "- **Caveat:** parser coverage and score sparsity measure the "
            "current rule set, not the fraction of game mechanics that are "
            "important. Unparsed lines receive zero score under current "
            "production semantics.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree",
        type=Path,
        default=DEFAULT_TREE_PATH,
        help=(
            "Path to a passive-tree export JSON file. Omit only when the raw "
            "data directory contains exactly one JSON file."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "Write to this path. Markdown defaults to "
            "docs/tree-data-analysis.md; JSON defaults to stdout."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.tree is None:
        candidate_names = ", ".join(
            path.name for path in DEFAULT_TREE_CANDIDATES
        ) or "<none>"
        raise SystemExit(
            "--tree is required unless src/poe_pathing/data/raw contains "
            f"exactly one JSON file; found: {candidate_names}"
        )
    report = analyse(args.tree)
    rendered = (
        json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_markdown(report) + "\n"
    )
    output = args.output
    if output is None and args.format == "markdown":
        output = DEFAULT_REPORT_PATH
    if output is None:
        sys.stdout.write(rendered)
        return
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"Wrote deterministic {args.format} analysis to {output}")


if __name__ == "__main__":
    main()
