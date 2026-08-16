"""Load the compiled knowledge graph and make it walkable.

Thin by design. The snapshot on disk is the source of truth; this module reads
it once, builds the adjacency the retriever needs, caches the result against the
file's mtime, and answers neighbour queries. No extraction logic lives here —
that is ``graph_builder`` — and no retrieval policy — that is ``graph_rag``.

The one judgement it does make is **fan-out capping**. A concept like "waiting
period" is mentioned in dozens of chunks, and a traversal that follows every one
of them stops being retrieval and becomes a scan of the corpus. Neighbours are
therefore returned strongest-first and capped, so a step through a hub costs
what a step through a specific term costs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.config import KNOWLEDGE_GRAPH_PATH

#: Neighbours returned per node per relation, strongest first.
MAX_FANOUT_PER_RELATION = 12


class GraphUnavailable(RuntimeError):
    """No snapshot on disk, or the file on disk is not a usable graph."""


@dataclass(frozen=True)
class Adjacent:
    """One step out of a node."""

    node_id: str
    kind: str
    #: Edge strength already normalised to (0, 1]; 1.0 when the relation carries
    #: no weight of its own.
    weight: float
    #: True when the stored edge points away from the node being queried.
    outgoing: bool


@dataclass
class KnowledgeGraph:
    raw: dict[str, Any]
    nodes: dict[str, dict[str, Any]]
    adjacency: dict[str, list[Adjacent]]
    chunk_ids: set[str] = field(default_factory=set)
    concept_ids: set[str] = field(default_factory=set)

    @property
    def stats(self) -> dict[str, Any]:
        return self.raw.get("stats", {})

    @property
    def fingerprint(self) -> str:
        return str(self.raw.get("corpus_fingerprint", ""))

    def node(self, node_id: str) -> dict[str, Any] | None:
        return self.nodes.get(node_id)

    def neighbours(self, node_id: str) -> list[Adjacent]:
        return self.adjacency.get(node_id, [])


def _edge_weight(edge: dict[str, Any]) -> float:
    """Normalise whatever strength the edge carries into (0, 1].

    Each relation stores a different quantity — TF-IDF salience, cosine, NPMI —
    and the traversal needs one comparable number. Anything without a strength
    is a structural edge and is worth its full step.
    """
    kind = edge.get("kind")
    if kind == "mentions":
        # Salience is an unbounded TF-IDF product; squash rather than clip so
        # a very salient mention still outranks a merely salient one.
        salience = float(edge.get("salience", 1.0))
        return min(1.0, salience / (salience + 2.0) * 2.0)
    if kind == "similar_to":
        return max(0.05, min(1.0, float(edge.get("score", 0.2)) * 2.5))
    if kind == "co_occurs":
        return max(0.05, min(1.0, float(edge.get("npmi", 0.2))))
    return 1.0


def _build(raw: dict[str, Any]) -> KnowledgeGraph:
    nodes: dict[str, dict[str, Any]] = {}
    for node in raw.get("nodes", []):
        node_id = node.get("id")
        if isinstance(node_id, str):
            nodes[node_id] = node

    buckets: dict[tuple[str, str, bool], list[Adjacent]] = {}
    for edge in raw.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        kind = edge.get("kind")
        if not (isinstance(source, str) and isinstance(target, str) and isinstance(kind, str)):
            continue
        if source not in nodes or target not in nodes:
            continue
        weight = _edge_weight(edge)
        buckets.setdefault((source, kind, True), []).append(
            Adjacent(node_id=target, kind=kind, weight=weight, outgoing=True)
        )
        buckets.setdefault((target, kind, False), []).append(
            Adjacent(node_id=source, kind=kind, weight=weight, outgoing=False)
        )

    adjacency: dict[str, list[Adjacent]] = {}
    for (node_id, _kind, _outgoing), items in buckets.items():
        # Strongest first, id as the tie-break so a rebuild never reorders.
        items.sort(key=lambda item: (-item.weight, item.node_id))
        adjacency.setdefault(node_id, []).extend(items[:MAX_FANOUT_PER_RELATION])

    for items in adjacency.values():
        items.sort(key=lambda item: (-item.weight, item.kind, item.node_id))

    return KnowledgeGraph(
        raw=raw,
        nodes=nodes,
        adjacency=adjacency,
        chunk_ids={node_id for node_id, node in nodes.items() if node.get("type") == "chunk"},
        concept_ids={node_id for node_id, node in nodes.items() if node.get("type") == "concept"},
    )


#: (mtime, size, graph) — size guards against a rewrite inside the mtime resolution.
_cache: tuple[float, int, KnowledgeGraph] | None = None


def load_graph(path: Path | None = None) -> KnowledgeGraph:
    """Return the compiled graph, re-reading it only when the file changes."""
    global _cache

    target = path or KNOWLEDGE_GRAPH_PATH
    if not target.exists():
        raise GraphUnavailable(
            "No knowledge graph has been compiled yet. Build one with "
            "`python backend/scripts/build_knowledge_graph.py`."
        )

    stat = target.stat()
    if _cache is not None and _cache[0] == stat.st_mtime and _cache[1] == stat.st_size:
        return _cache[2]

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GraphUnavailable(f"The knowledge graph file is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict) or not raw.get("nodes"):
        raise GraphUnavailable("The knowledge graph file has no nodes. Rebuild it.")

    graph = _build(raw)
    _cache = (stat.st_mtime, stat.st_size, graph)
    return graph


def try_load_graph(path: Path | None = None) -> KnowledgeGraph | None:
    """``load_graph`` for callers that treat an absent graph as a normal state."""
    try:
        return load_graph(path)
    except GraphUnavailable:
        return None


def reset_cache() -> None:
    """Drop the cached graph. Tests that write a snapshot need this."""
    global _cache
    _cache = None


def chunk_nodes(graph: KnowledgeGraph, chunk_ids: Iterable[str]) -> list[dict[str, Any]]:
    resolved = [graph.nodes.get(chunk_id) for chunk_id in chunk_ids]
    return [node for node in resolved if node is not None]
