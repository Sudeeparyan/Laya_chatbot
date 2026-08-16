"""Graph-expansion retrieval over the compiled knowledge graph.

The problem this exists to solve
--------------------------------
Lexical retrieval scores each passage on its own. That is fine when the answer
sits inside one passage, and it fails in a specific, repeatable way when it does
not — which, in a corpus of benefit schedules, is often:

* **The figure and its qualifier are in different chunks.** A table header row
  naming the treatment lands in one chunk and the row carrying "70% refund,
  1 per surface every 2 years" lands in the next. Scored alone, the second chunk
  contains almost none of the question's words.
* **The answer is assembled from two documents.** "What excess applies to a
  knee arthroscopy?" needs the procedure from the day-case schedule and the
  excess amount from the hospital cover document. Neither chunk answers it.
* **The question names the concept, the passage names an instance.** A question
  about "waiting periods" has to reach a passage that only ever says "52 weeks
  from the member's join date".

Each of these is a *connection* the corpus already contains and a bag-of-words
score cannot see. The knowledge graph records those connections explicitly, so
the fix is to retrieve over the graph rather than only over the text.

How it works
------------
1. **Seed** with the existing BM25 pass. Lexical retrieval is good at finding a
   starting point, so it is kept rather than replaced — the graph is an
   expansion stage, not a competing retriever.
2. **Link** the question's terms to concept nodes, so a question naming a
   concept can enter the graph even where no single passage scores well.
3. **Propagate** relevance mass outward for two hops along typed edges, with a
   per-relation weight and a per-hop decay. Two hops is what the useful patterns
   need — ``chunk → concept → chunk`` for a shared term, ``chunk → section →
   chunk`` for a sibling passage — and a third mostly adds noise.
4. **Fuse** the normalised lexical score with the normalised graph score, and
   re-rank on the result.

What it deliberately does not do
--------------------------------
Expansion never opens the gate. If no seed passage clears the coverage
threshold, the answer is still the "I could not find this" fallback: a graph
walk that starts from nothing relevant arrives at nothing relevant, and letting
it produce context anyway would reintroduce exactly the hallucination risk the
gate exists to remove. The graph widens the evidence behind an answer; it never
manufactures one.

Every stage is recorded in a trace, so a returned passage can always be
explained as "seeded by BM25 at rank 3" or "reached from chunk 12 through the
concept *waiting period*" — and the same trace is what the graph view replays.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.services.graph_store import Adjacent, KnowledgeGraph
from app.services.rag_service import Hit, _tokenize

# --------------------------------------------------------------------------
# Tuning
# --------------------------------------------------------------------------

#: Hops of propagation outward from the seeds.
HOPS = 2
#: Relevance retained per hop. Steep on purpose: a passage two steps out has to
#: be strongly connected to compete with one the question actually matched.
HOP_DECAY = 0.55
#: Mass below which a branch is not worth following.
MIN_MASS = 0.008
#: Seeds taken from the lexical pass.
SEED_LIMIT = 8
#: Mass injected per concept the question names, relative to the top seed.
CONCEPT_SEED_MASS = 0.45
#: Weight on lexical score when the two are fused. The graph is a correction to
#: the lexical ranking, not a replacement for it, so the lexical side keeps the
#: larger share.
LEXICAL_WEIGHT = 0.6
GRAPH_WEIGHT = 1.0 - LEXICAL_WEIGHT

#: How much relevance survives a step along each relation.
#:
#: ``contains`` and ``governed_by`` are 0 — deliberately. A document node and a
#: department node are true facts about the corpus and belong in the picture,
#: but stepping through them would make every passage in a document relevant to
#: every other, which is not retrieval. They stay visible and unwalked.
RELATION_WEIGHT: dict[str, float] = {
    "mentions": 1.00,
    "similar_to": 0.80,
    "follows": 0.55,
    "co_occurs": 0.45,
    "has_chunk": 0.30,
    "in_community": 0.20,
    "contains": 0.0,
    "governed_by": 0.0,
}

#: Phrasing for the "why is this here" line on an expanded passage.
_STEP_PHRASE: dict[str, str] = {
    "mentions": "shares the term",
    "similar_to": "closely similar wording to",
    "follows": "directly adjacent to",
    "co_occurs": "related term to",
    "has_chunk": "same section as",
    "in_community": "same theme as",
}


@dataclass
class GraphHit:
    """One passage after expansion and fusion."""

    chunk_id: str
    lexical: float
    graph: float
    fused: float
    coverage: float
    origin: str  #: "seed" or "expanded"
    reason: str
    hops: int = 0


@dataclass
class _Provenance:
    """The single strongest way relevance reached a node."""

    source_id: str
    kind: str
    contribution: float
    hop: int


@dataclass
class GraphRetrieval:
    hits: list[GraphHit]
    trace: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Query → concept linking
# --------------------------------------------------------------------------


def link_concepts(question: str, graph: KnowledgeGraph) -> list[tuple[str, list[str]]]:
    """Concept nodes the question names, with the query terms that matched them.

    A concept's stored normal form is already stemmed with the retriever's own
    stemmer, so matching is a set test against the stemmed question. Multi-word
    concepts must match in full — "restorative treatment" should not fire on a
    question that merely says "treatment" — while single words match exactly.
    Contiguity is not required, so "treatment, restorative" still matches.
    """
    query_terms = set(_tokenize(question))
    if not query_terms:
        return []

    matched: list[tuple[str, list[str], int]] = []
    for concept_id in graph.concept_ids:
        node = graph.nodes[concept_id]
        normal_form = str(node.get("normal_form") or "")
        parts = [part for part in normal_form.split() if part]
        if not parts:
            continue
        if all(part in query_terms for part in parts):
            matched.append((concept_id, parts, len(parts)))

    # Longest first: a question matching both "waiting period" and "period"
    # should be led by the specific one.
    matched.sort(key=lambda item: (-item[2], item[0]))
    return [(concept_id, parts) for concept_id, parts, _ in matched]


# --------------------------------------------------------------------------
# Propagation
# --------------------------------------------------------------------------


#: Entity types whose specificity scales a step through them.
_ENTITY_TYPES = frozenset({"concept", "value", "constraint"})


def _step_weight(graph: KnowledgeGraph, source_id: str, edge: Adjacent) -> float:
    """How much relevance survives one step, given where it is stepping from.

    Beyond the relation's own weight, a step *out of* an entity is scaled by
    that entity's specificity. This is what stops expansion from running through
    hubs: a passage about knee arthroscopy and a passage about maternity cover
    both mention "refund", so relevance that travels along "refund" is not
    evidence of anything. Travelling along "knee arthroscopy" is.
    """
    base = RELATION_WEIGHT.get(edge.kind, 0.0)
    if not base:
        return 0.0

    weight = base * edge.weight
    source = graph.node(source_id)
    if source is not None and source.get("type") in _ENTITY_TYPES:
        weight *= float(source.get("specificity", 1.0))
    return weight


def propagate(
    graph: KnowledgeGraph,
    seed_mass: dict[str, float],
    hops: int = HOPS,
) -> tuple[dict[str, float], dict[str, _Provenance], list[dict[str, Any]]]:
    """Spread relevance outward from the seeds, recording how it travelled.

    Returns the accumulated mass per node, the strongest inbound step per node,
    and every edge the walk actually used — the last of which is what the graph
    view highlights.
    """
    mass: dict[str, float] = dict(seed_mass)
    provenance: dict[str, _Provenance] = {}
    traversed: dict[tuple[str, str, str], dict[str, Any]] = {}
    frontier: dict[str, float] = dict(seed_mass)

    for hop in range(hops):
        decay = HOP_DECAY ** hop
        next_frontier: dict[str, float] = defaultdict(float)

        for node_id in sorted(frontier):
            node_mass = frontier[node_id]
            if node_mass < MIN_MASS:
                continue
            for edge in graph.neighbours(node_id):
                weight = _step_weight(graph, node_id, edge)
                if weight <= 0:
                    continue
                contribution = node_mass * weight * decay
                if contribution < MIN_MASS:
                    continue
                # Never push relevance back into a seed; it is already there,
                # and a two-way exchange between two seeds inflates both.
                if edge.node_id in seed_mass:
                    continue
                next_frontier[edge.node_id] += contribution
                key = (node_id, edge.node_id, edge.kind)
                if key not in traversed:
                    traversed[key] = {
                        "source": node_id,
                        "target": edge.node_id,
                        "kind": edge.kind,
                        "hop": hop + 1,
                        "weight": round(weight, 4),
                    }
                existing = provenance.get(edge.node_id)
                if existing is None or contribution > existing.contribution:
                    provenance[edge.node_id] = _Provenance(
                        source_id=node_id, kind=edge.kind, contribution=contribution, hop=hop + 1
                    )

        if not next_frontier:
            break
        for node_id, gained in next_frontier.items():
            mass[node_id] = mass.get(node_id, 0.0) + gained
        frontier = dict(next_frontier)

    return mass, provenance, list(traversed.values())


# --------------------------------------------------------------------------
# Explanation
# --------------------------------------------------------------------------


def _describe(graph: KnowledgeGraph, chunk_id: str, provenance: dict[str, _Provenance]) -> str:
    """One sentence saying how this passage was reached.

    Walks back at most two steps, which is as far as propagation goes, and
    names the intermediate node when there is one — "shares the term *waiting
    period* with Meridian Hospital Cover" says something; "reached in 2 hops"
    does not.
    """
    step = provenance.get(chunk_id)
    if step is None:
        return "matched the question directly"

    via = graph.node(step.source_id)
    phrase = _STEP_PHRASE.get(step.kind, "connected to")

    if via is None:
        return f"{phrase} another passage"

    via_type = via.get("type")
    here = graph.node(chunk_id) or {}

    def name(node: dict[str, Any]) -> str:
        """Identify a passage the way a reader would look for it.

        Naming the document is only useful when it is a *different* document.
        "shares a term with Meridian Day-Case Schedule" is confusing when the
        passage being explained is itself from that schedule, so a same-document
        neighbour is named by its section instead.
        """
        if node.get("document_id") and node.get("document_id") != here.get("document_id"):
            return str(node.get("document_title") or "another document")
        heading = node.get("heading") or node.get("label")
        return f"“{heading}”" if heading else "another passage here"

    if via_type in ("concept", "value", "constraint"):
        # The intermediate is a term; name the passage on the far side of it.
        origin = provenance.get(step.source_id)
        anchor = graph.node(origin.source_id) if origin else None
        label = via.get("label", "a term")
        if anchor is not None and anchor.get("type") == "chunk":
            return f"shares the term “{label}” with {name(anchor)}"
        return f"mentions “{label}”, which the question names"

    if via_type == "section":
        return f"same section as a matched passage — {via.get('label', 'section')}"

    if via_type == "chunk":
        return f"{phrase} {name(via)}"

    return f"{phrase} {via.get('label', 'a matched node')}"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def _normalise(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    top = max(scores.values())
    if top <= 0:
        return {key: 0.0 for key in scores}
    return {key: value / top for key, value in scores.items()}


def retrieve(
    question: str,
    lexical_hits: list[Hit],
    graph: KnowledgeGraph,
    top_k: int,
) -> GraphRetrieval:
    """Expand the lexical hits over the graph and re-rank the union."""
    seeds = lexical_hits[:SEED_LIMIT]
    lexical_scores = {hit.chunk.chunk_id: hit.score for hit in seeds}
    coverage_by_chunk = {hit.chunk.chunk_id: hit.coverage for hit in lexical_hits}
    lexical_norm = _normalise(lexical_scores)

    seed_mass: dict[str, float] = dict(lexical_norm)
    linked = link_concepts(question, graph)
    for concept_id, _parts in linked:
        # Only inject concepts the graph actually holds; a concept with no
        # mentions cannot lead anywhere.
        if concept_id in graph.nodes:
            seed_mass.setdefault(concept_id, CONCEPT_SEED_MASS)

    mass, provenance, traversed = propagate(graph, seed_mass)

    graph_scores = {
        node_id: value
        for node_id, value in mass.items()
        if node_id in graph.chunk_ids and node_id not in lexical_norm
    }
    graph_norm = _normalise(graph_scores)

    candidates = set(lexical_norm) | set(graph_norm)
    hits: list[GraphHit] = []
    for chunk_id in candidates:
        lexical = lexical_norm.get(chunk_id, 0.0)
        graph_score = graph_norm.get(chunk_id, 0.0)
        is_seed = chunk_id in lexical_norm
        step = provenance.get(chunk_id)
        hits.append(
            GraphHit(
                chunk_id=chunk_id,
                lexical=round(lexical, 4),
                graph=round(graph_score, 4),
                fused=round(LEXICAL_WEIGHT * lexical + GRAPH_WEIGHT * graph_score, 4),
                coverage=coverage_by_chunk.get(chunk_id, 0.0),
                origin="seed" if is_seed else "expanded",
                reason=(
                    "matched the question directly"
                    if is_seed
                    else _describe(graph, chunk_id, provenance)
                ),
                hops=0 if is_seed else (step.hop if step else HOPS),
            )
        )

    hits.sort(key=lambda hit: (-hit.fused, hit.chunk_id))
    selected = hits[:top_k]
    selected_ids = {hit.chunk_id for hit in selected}

    # Trim the trace to the walk that actually produced the selected passages —
    # the full frontier is large and nothing downstream reads the rest of it.
    kept_nodes: set[str] = set(seed_mass) | selected_ids
    for chunk_id in selected_ids:
        cursor: str | None = chunk_id
        for _ in range(HOPS):
            step = provenance.get(cursor) if cursor else None
            if step is None:
                break
            kept_nodes.add(step.source_id)
            cursor = step.source_id

    kept_edges = [
        edge
        for edge in traversed
        if edge["source"] in kept_nodes and edge["target"] in kept_nodes
    ]

    trace = {
        "seeds": [
            {
                "chunk_id": hit.chunk.chunk_id,
                "document_title": hit.chunk.document_title,
                "heading": hit.chunk.heading,
                "bm25": hit.score,
                "coverage": hit.coverage,
                "rank": rank,
            }
            for rank, hit in enumerate(seeds, start=1)
        ],
        "linked_concepts": [
            {
                "id": concept_id,
                "label": graph.nodes[concept_id].get("label", concept_id),
                "matched_terms": parts,
            }
            for concept_id, parts in linked[:10]
            if concept_id in graph.nodes
        ],
        "selected": [
            {
                "chunk_id": hit.chunk_id,
                "origin": hit.origin,
                "lexical": hit.lexical,
                "graph": hit.graph,
                "fused": hit.fused,
                "hops": hit.hops,
                "reason": hit.reason,
            }
            for hit in selected
        ],
        "highlight_nodes": sorted(kept_nodes),
        "highlight_edges": kept_edges,
        "counts": {
            "seed_chunks": len(seeds),
            "linked_concepts": len(linked),
            "nodes_reached": len(mass),
            "edges_traversed": len(traversed),
            "expanded_chunks": len(graph_norm),
            "selected_expanded": sum(1 for hit in selected if hit.origin == "expanded"),
        },
        "settings": {
            "hops": HOPS,
            "hop_decay": HOP_DECAY,
            "lexical_weight": LEXICAL_WEIGHT,
            "graph_weight": GRAPH_WEIGHT,
            "seed_limit": SEED_LIMIT,
        },
    }

    return GraphRetrieval(hits=selected, trace=trace)
