"""The compiled knowledge graph, and the endpoint that serves it.

The claim the graph has to support is that it is a faithful, reproducible
description of the corpus — not a decorative picture. These tests check the
properties that claim actually rests on: every edge lands on a node that exists,
nothing is left dangling, the same corpus always compiles to the same graph, and
the mined vocabulary does not contain phrases the corpus never said.

That last one has teeth. The converter renders tables as Markdown pipe rows, so
a naive n-gram pass invents terms by running across cell boundaries. It is the
single most likely way for this pipeline to start describing a corpus that does
not exist, so it is tested directly rather than eyeballed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import KNOWLEDGE_GRAPH_PATH
from app.main import app
from app.services import rag_service
from app.services.graph_builder import (
    EDGE_TYPE_DESCRIPTIONS,
    NODE_TYPE_DESCRIPTIONS,
    _is_valid_phrase,
    _segments,
    _specificity,
    build_graph,
    extract_pattern_entities,
    mine_concepts,
)

# --------------------------------------------------------------------------
# A miniature corpus, so the structural tests do not depend on the shipped one
# --------------------------------------------------------------------------

_DOC_A = """---
document_id: "plan-a"
document_title: "Plan A Dental"
---

# Plan A Dental

## Preventative Treatment

Preventative treatment carries a waiting period of 6 months from the join date.
The annual dental limit is 620 euro per member.

| Treatment | Refund | Frequency limit |
| --- | --- | --- |
| Routine examination | 100% refund | 2 per year |
| Scaling and polishing | 80% refund | 1 per year |

## Orthodontic Treatment

Orthodontic treatment requires pre-authorisation and a waiting period of 24 months.
The lifetime orthodontic limit is 2,200 euro per member.
"""

_DOC_B = """---
document_id: "plan-b"
document_title: "Plan B Hospital"
---

# Plan B Hospital

## Day-Case Treatment

Day-case treatment carries an excess of 75 euro per admission and requires
pre-authorisation. A waiting period of 26 weeks applies to new members.

| Procedure | Co-payment | Frequency limit |
| --- | --- | --- |
| Knee arthroscopy | 120 euro | 1 per joint per year |
| Cataract removal | 200 euro | 1 per eye |
"""


@pytest.fixture()
def small_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Index two tiny documents, isolated from the real corpus."""
    markdown_dir = tmp_path / "markdown_outputs"
    markdown_dir.mkdir(parents=True, exist_ok=True)
    (markdown_dir / "plan-a.md").write_text(_DOC_A, encoding="utf-8")
    (markdown_dir / "plan-b.md").write_text(_DOC_B, encoding="utf-8")

    monkeypatch.setattr(rag_service, "MARKDOWN_OUTPUT_DIR", markdown_dir)
    monkeypatch.setattr(rag_service, "MANIFEST_DIR", tmp_path / "manifests")
    return rag_service.build_index(force=True)


@pytest.fixture()
def small_graph(small_index) -> dict:
    return build_graph(small_index)


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_every_edge_lands_on_a_node_that_exists(small_graph: dict) -> None:
    ids = {node["id"] for node in small_graph["nodes"]}
    for edge in small_graph["edges"]:
        assert edge["source"] in ids, f"dangling source on {edge['id']}"
        assert edge["target"] in ids, f"dangling target on {edge['id']}"
        assert edge["source"] != edge["target"], f"self-loop on {edge['id']}"


def test_no_isolated_nodes(small_graph: dict) -> None:
    """A node with no edges is in every count and connected to nothing.

    Concepts are the ones at risk: a term can clear the corpus-wide extraction
    bar and still lose every per-chunk salience cut, which used to leave it
    stranded in the graph.
    """
    degree = {node["id"]: 0 for node in small_graph["nodes"]}
    for edge in small_graph["edges"]:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    stranded = [node_id for node_id, count in degree.items() if count == 0]
    assert stranded == []


def test_declared_types_match_what_was_built(small_graph: dict) -> None:
    node_types = {node["type"] for node in small_graph["nodes"]}
    edge_kinds = {edge["kind"] for edge in small_graph["edges"]}
    assert node_types <= set(NODE_TYPE_DESCRIPTIONS)
    assert edge_kinds <= set(EDGE_TYPE_DESCRIPTIONS)


def test_structural_backbone_is_present(small_graph: dict) -> None:
    """Both documents, their sections and their chunks are all wired up."""
    by_type: dict[str, list[dict]] = {}
    for node in small_graph["nodes"]:
        by_type.setdefault(node["type"], []).append(node)

    assert len(by_type["document"]) == 2
    assert by_type["section"], "no sections were built"
    assert by_type["chunk"], "no chunks were built"

    kinds = {edge["kind"] for edge in small_graph["edges"]}
    assert {"contains", "has_chunk", "mentions"} <= kinds


def test_stats_agree_with_the_arrays(small_graph: dict) -> None:
    stats = small_graph["stats"]
    assert stats["nodes"] == len(small_graph["nodes"])
    assert stats["edges"] == len(small_graph["edges"])
    assert sum(stats["nodes_by_type"].values()) == len(small_graph["nodes"])
    assert sum(stats["edges_by_kind"].values()) == len(small_graph["edges"])


def test_the_same_corpus_compiles_to_the_same_graph(small_index) -> None:
    """Reproducibility. Only the wall-clock stamp may differ between runs."""
    first = build_graph(small_index)
    second = build_graph(small_index)
    for graph in (first, second):
        graph.pop("generated_at_utc")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --------------------------------------------------------------------------
# Term extraction
# --------------------------------------------------------------------------


def test_phrases_never_cross_a_table_cell_boundary() -> None:
    """The defect this guards against produced terms no document contains.

    "| Scaling and polishing | 80% refund | 1 per year |" is one line of text.
    Mined without segmenting, it yields "polishing refund per year" — a phrase
    that reads exactly like a real term and is pure artefact.
    """
    row = "| Scaling and polishing | 80% refund | 1 per year |"
    segments = _segments(row)
    joined = [" ".join(words) for words in segments]
    assert "Scaling and polishing" in joined
    assert not any("polishing" in text and "refund" in text for text in joined)


def test_sentence_punctuation_also_breaks_a_phrase() -> None:
    segments = _segments("the waiting period applies. Orthodontic treatment is covered")
    assert not any("applies" in " ".join(words) and "Orthodontic" in " ".join(words) for words in segments)


def test_function_words_and_verbs_are_rejected() -> None:
    assert _is_valid_phrase(["waiting", "period"])
    assert _is_valid_phrase(["knee", "arthroscopy"])
    # "per" is a function word; "applies" is verbal.
    assert not _is_valid_phrase(["refund", "per", "surface"])
    assert not _is_valid_phrase(["applies"])
    assert not _is_valid_phrase(["treatment", "is", "covered"])


def test_mined_concepts_are_real_domain_terms(small_index) -> None:
    mined = mine_concepts(small_index.chunks)
    surfaces = {candidate.surface for candidate in mined.values()}
    assert "waiting period" in surfaces
    # Nothing verbal or fragmentary survives.
    assert not any(surface.startswith(("is ", "are ", "applies")) for surface in surfaces)


def test_concept_labels_use_the_dominant_surface_form(small_index) -> None:
    """A stem key must not display as whichever inflection was read first."""
    mined = mine_concepts(small_index.chunks)
    for candidate in mined.values():
        assert candidate.surface in candidate.surfaces
        assert candidate.surfaces[candidate.surface] == max(candidate.surfaces.values())


# --------------------------------------------------------------------------
# Pattern entities
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_type, expected_surface",
    [
        ("the annual dental limit is 620 euro", "value", "€620"),
        ("refunded at 70% refund", "value", "70% refund"),
        ("covered 2 per year", "constraint", "2 per year"),
        ("1 every 5 years", "constraint", "1 every 5 years"),
        ("a waiting period of 24 months applies", "constraint", "waiting period of 24 months"),
        ("up to the age of 18", "constraint", "up to the age of 18"),
    ],
)
def test_pattern_entities(text: str, expected_type: str, expected_surface: str) -> None:
    found = extract_pattern_entities(text)
    assert (expected_type, expected_surface) in [
        (node_type, surface) for node_type, _subtype, surface in found
    ]


def test_money_is_normalised_so_one_amount_is_one_node() -> None:
    """"2,200 euro" and "€2,200" are the same figure and must share a node."""
    written = extract_pattern_entities("the limit is 2,200 euro per member")
    symbol = extract_pattern_entities("the limit is €2,200 per member")
    assert written[0][2] == symbol[0][2] == "€2,200"


# --------------------------------------------------------------------------
# Specificity
# --------------------------------------------------------------------------


def test_specificity_falls_as_a_term_spreads() -> None:
    """The property the retriever relies on to avoid walking through hubs."""
    rare = _specificity(1, 60)
    common = _specificity(30, 60)
    everywhere = _specificity(60, 60)
    assert rare > common > everywhere
    assert everywhere == pytest.approx(0.0, abs=1e-6)
    assert 0.0 <= common <= 1.0


def test_entities_carry_specificity(small_graph: dict) -> None:
    entities = [n for n in small_graph["nodes"] if n["type"] in ("concept", "value", "constraint")]
    assert entities
    for node in entities:
        assert 0.0 <= node["specificity"] <= 1.0


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not KNOWLEDGE_GRAPH_PATH.exists(),
    reason="No graph compiled — run backend/scripts/build_knowledge_graph.py",
)
def test_endpoint_serves_the_compiled_graph() -> None:
    response = TestClient(app).get("/api/graph/knowledge")
    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] and body["edges"]
    assert body["stats"]["nodes"] == len(body["nodes"])
    # The freshness flag the view uses to warn that an upload is not yet indexed.
    assert "stale" in body
