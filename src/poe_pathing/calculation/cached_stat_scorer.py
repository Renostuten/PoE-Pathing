from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .stat_parser import ParsedStat, StatParser
from .stat_scorer import StatScorer
from tree.node_lookup import NodeLookup


StatKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class NodeStatVector:
    """The supported parsed contributions for one passive node.

    Contributions deliberately remain ordered rather than being combined by
    key.  Scoring therefore performs the same multiplications and additions in
    the same order as :class:`StatScorer`, preserving its floating-point
    semantics while avoiding repeated parsing of the raw stat strings.
    """

    node_id: str
    contributions: tuple[ParsedStat, ...]

    def score(self, desired_stats: Mapping[StatKey, float]) -> float:
        total = 0.0

        for contribution in self.contributions:
            key = (contribution.stat_type, contribution.modifier_type)
            weight = desired_stats.get(key, 0.0)
            total += contribution.value * weight

        return total


class CachedStatScorer(StatScorer):
    """A ``StatScorer`` that parses each node's supported stats only once.

    ``node_ids`` may restrict eager materialisation to nodes used by a graph.
    Any valid node outside that set is materialised on its first score request,
    so the existing scorer's behaviour is preserved.  Omitting ``node_ids``
    eagerly materialises all nodes exposed by the production ``NodeLookup``.
    """

    def __init__(
        self,
        stat_parser: StatParser,
        node_lookup: NodeLookup,
        node_ids: Iterable[str] | None = None,
    ) -> None:
        super().__init__(stat_parser, node_lookup)
        self._vectors: dict[str, NodeStatVector] = {}
        self.node_vectors: Mapping[str, NodeStatVector] = MappingProxyType(
            self._vectors
        )

        eager_node_ids = (
            self._node_ids_from_lookup(node_lookup)
            if node_ids is None
            else node_ids
        )
        for node_id in sorted(set(eager_node_ids), key=str):
            self._vectors[node_id] = self._build_vector(node_id)

    @property
    def cached_node_count(self) -> int:
        """Return the number of node vectors currently materialised."""

        return len(self._vectors)

    def vector_for(self, node_id: str) -> NodeStatVector:
        """Return a cached vector, materialising a valid uncached node once."""

        vector = self._vectors.get(node_id)
        if vector is None:
            vector = self._build_vector(node_id)
            self._vectors[node_id] = vector
        return vector

    def parsed_stats_for_node(self, node_id: str) -> tuple[ParsedStat, ...]:
        """Return cached parsed lines for response stat aggregation."""

        return self.vector_for(node_id).contributions

    def score_node(
        self,
        node_id: str,
        desired_stats: dict[StatKey, float],
    ) -> float:
        return self.vector_for(node_id).score(desired_stats)

    def _build_vector(self, node_id: str) -> NodeStatVector:
        node = self.node_lookup.get(node_id)
        contributions = tuple(
            parsed
            for raw_stat in node.stats
            if (parsed := self.stat_parser.parse(raw_stat)) is not None
        )
        return NodeStatVector(node_id, contributions)

    @staticmethod
    def _node_ids_from_lookup(node_lookup: NodeLookup) -> Iterable[str]:
        lookup = getattr(node_lookup, "lookup", None)
        if not isinstance(lookup, Mapping):
            raise TypeError(
                "node_ids is required when node_lookup does not expose "
                "a mapping named 'lookup'"
            )
        return lookup.keys()
