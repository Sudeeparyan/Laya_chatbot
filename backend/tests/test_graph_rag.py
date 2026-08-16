"""Graph-expansion retrieval.

Two things are being defended here.

The first is that expansion works: a passage the question does not lexically
match, but which the corpus connects to one it does, should be retrievable.
That is the whole reason the graph exists.

The second matters more. Expansion must never widen the *gate*. If the lexical
pass finds nothing relevant, a graph walk starts from nowhere, and every passage
it reaches is reached by association alone — handing that to the model as
context is how a retrieval system produces a confident answer to a question its
corpus cannot answer. The gate is checked before expansion, and there is a test
below that fails loudly if that ever stops being true.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import graph_rag, graph_store, rag_service
from app.services.graph_builder import build_graph

_DOC_DENTAL = """---
document_id: "dental"
document_title: "Dental Schedule"
---

# Dental Schedule

## Orthodontic Treatment

Orthodontic treatment requires pre-authorisation and carries a waiting period
of 24 months. The lifetime orthodontic limit is 2,200 euro per member.

## Major Restorative Treatment

Root canal treatment is refunded at 70% refund, 1 per tooth every 5 years.
A porcelain crown is refunded at 60% refund, 1 per tooth every 7 years.
"""

_DOC_HOSPITAL = """---
document_id: "hospital"
document_title: "Hospital Cover"
---

# Hospital Cover

## Day-Case Treatment

Day-case treatment carries an excess of 75 euro per admission.
A knee arthroscopy requires pre-authorisation.

## Waiting Periods

A waiting period of 26 weeks applies to all day-case benefits for new members.
Waiting periods carry over where a member transfers without a break in cover.
"""


@pytest.fixture()
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two documents indexed and compiled into a graph on disk."""
    markdown_dir = tmp_path / "markdown_outputs"
    markdown_dir.mkdir(parents=True, exist_ok=True)
    (markdown_dir / "dental.md").write_text(_DOC_DENTAL, encoding="utf-8")
    (markdown_dir / "hospital.md").write_text(_DOC_HOSPITAL, encoding="utf-8")

    monkeypatch.setattr(rag_service, "MARKDOWN_OUTPUT_DIR", markdown_dir)
    monkeypatch.setattr(rag_service, "MANIFEST_DIR", tmp_path / "manifests")
    # Retrieval is what is under test, and a developer machine may well have
    # Azure OpenAI credentials in .env. Left alone these tests would make real
    # network calls — slow, billable, and failing for reasons that have nothing
    # to do with the graph. With generation switched off, `answer_question`
    # takes its documented "no model configured" path and still returns the
    # retrieved passages, which is exactly what needs asserting.
    monkeypatch.setattr(rag_service, "is_plugin_available", lambda: False)
    index = rag_service.build_index(force=True)

    snapshot = tmp_path / "knowledge_graph.json"
    snapshot.write_text(json.dumps(build_graph(index)), encoding="utf-8")

    graph_store.reset_cache()
    monkeypatch.setattr(graph_store, "KNOWLEDGE_GRAPH_PATH", snapshot)
    graph = graph_store.load_graph(snapshot)
    yield index, graph
    graph_store.reset_cache()


# --------------------------------------------------------------------------
# Concept linking
# --------------------------------------------------------------------------


def test_a_multiword_concept_needs_every_token(corpus) -> None:
    """"treatment" alone must not fire the "orthodontic treatment" concept."""
    _index, graph = corpus
    full = {label for _id, _parts in graph_rag.link_concepts("orthodontic treatment", graph) for label in [_id]}
    partial_labels = [
        graph.nodes[concept_id]["label"]
        for concept_id, _parts in graph_rag.link_concepts("treatment", graph)
    ]
    assert full
    assert "orthodontic treatment" not in partial_labels


def test_linking_survives_inflection_and_word_order(corpus) -> None:
    _index, graph = corpus
    labels = [
        graph.nodes[concept_id]["label"]
        for concept_id, _parts in graph_rag.link_concepts("what are the waiting periods?", graph)
    ]
    assert "waiting period" in labels


# --------------------------------------------------------------------------
# Expansion
# --------------------------------------------------------------------------


def test_expansion_reaches_passages_bm25_ranked_below_the_cut(corpus) -> None:
    _index, graph = corpus
    question = "waiting period"
    lexical = rag_service.search(question, top_k=8)
    result = graph_rag.retrieve(question, lexical, graph, top_k=6)

    assert result.hits
    assert any(hit.origin == "expanded" for hit in result.hits), "the graph added nothing"
    for hit in result.hits:
        assert hit.reason, "every passage must say how it was reached"


def test_results_are_ordered_by_the_fused_score(corpus) -> None:
    _index, graph = corpus
    lexical = rag_service.search("pre-authorisation", top_k=8)
    result = graph_rag.retrieve("pre-authorisation", lexical, graph, top_k=6)
    scores = [hit.fused for hit in result.hits]
    assert scores == sorted(scores, reverse=True)


def test_fusion_weights_are_applied_as_documented(corpus) -> None:
    _index, graph = corpus
    lexical = rag_service.search("waiting period", top_k=8)
    result = graph_rag.retrieve("waiting period", lexical, graph, top_k=6)
    for hit in result.hits:
        expected = graph_rag.LEXICAL_WEIGHT * hit.lexical + graph_rag.GRAPH_WEIGHT * hit.graph
        assert hit.fused == pytest.approx(expected, abs=1e-3)


def test_hub_terms_carry_less_relevance_than_specific_ones(corpus) -> None:
    """The property that keeps expansion from wandering the whole corpus."""
    _index, graph = corpus
    entities = [
        node
        for node in graph.nodes.values()
        if node.get("type") == "concept" and node.get("chunk_frequency")
    ]
    assert entities
    spread = max(entities, key=lambda node: node["chunk_frequency"])
    narrow = min(entities, key=lambda node: node["chunk_frequency"])
    if spread["chunk_frequency"] > narrow["chunk_frequency"]:
        assert spread["specificity"] < narrow["specificity"]


def test_the_trace_only_highlights_nodes_the_graph_holds(corpus) -> None:
    _index, graph = corpus
    lexical = rag_service.search("excess", top_k=8)
    result = graph_rag.retrieve("excess", lexical, graph, top_k=6)
    for node_id in result.trace["highlight_nodes"]:
        assert node_id in graph.nodes
    for edge in result.trace["highlight_edges"]:
        assert edge["source"] in graph.nodes and edge["target"] in graph.nodes


# --------------------------------------------------------------------------
# The gate — expansion must not widen it
# --------------------------------------------------------------------------


def test_an_unanswerable_question_still_falls_back_in_graph_mode(corpus) -> None:
    """The load-bearing guarantee.

    Nothing in this corpus concerns orbital mechanics. Graph mode must refuse
    it, rather than walking outward from weak seeds and assembling
    plausible-looking context.
    """
    response = rag_service.answer_question(
        "explain orbital mechanics and rocket staging", mode="graph"
    )
    assert response.answered is False
    assert response.fallback_reason == "no_relevant_passages"
    assert response.sources == []


@pytest.mark.parametrize(
    "question",
    [
        "explain orbital mechanics and rocket staging",  # nothing matches at all
        "what is the windscreen excess on my motor policy?",  # one term matches
        "waiting period",  # squarely answerable
        "how much is a porcelain crown refunded?",
    ],
)
def test_expansion_never_changes_the_answerability_decision(corpus, question: str) -> None:
    """Graph mode may retrieve *more*, never *instead*.

    Stated as parity with core mode rather than as an absolute, because
    answerability is decided by the lexical coverage gate and that gate has a
    known soft spot: query terms absent from the index are dropped from the
    denominator, so "windscreen excess on my motor policy" scores full coverage
    on the strength of "excess" alone. That is a property of the core retriever
    and is out of scope here — what must hold is that expansion neither widens
    nor narrows it.
    """
    core = rag_service.answer_question(question, mode="core")
    graph = rag_service.answer_question(question, mode="graph")
    refused = "no_relevant_passages"
    assert (core.fallback_reason == refused) == (graph.fallback_reason == refused)


def test_graph_mode_reports_what_it_did(corpus) -> None:
    response = rag_service.answer_question("waiting period", mode="graph")
    report = response.retrieval
    assert report is not None
    assert report.requested_mode == "graph"
    assert report.mode == "graph"
    assert report.fell_back is False
    assert report.nodes_reached > 0


def test_core_mode_does_not_touch_the_graph(corpus) -> None:
    response = rag_service.answer_question("waiting period", mode="core")
    report = response.retrieval
    assert report is not None
    assert report.mode == "core"
    assert report.nodes_reached == 0
    assert report.highlight_nodes == []
    for source in response.sources:
        assert source.origin is None
        assert source.graph_score is None


def test_graph_mode_degrades_to_core_when_no_snapshot_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpus
) -> None:
    """A missing graph must not break chat — it downgrades and says so."""
    graph_store.reset_cache()
    monkeypatch.setattr(graph_store, "KNOWLEDGE_GRAPH_PATH", tmp_path / "absent.json")

    response = rag_service.answer_question("waiting period", mode="graph")
    report = response.retrieval
    assert report is not None
    assert report.mode == "core"
    assert report.fell_back is True
    assert report.fallback_reason == "no_graph_snapshot"
    # Still a usable answer path, just without expansion.
    assert response.sources


def test_an_unknown_mode_is_treated_as_graph(corpus) -> None:
    response = rag_service.answer_question("waiting period", mode="nonsense")
    assert response.retrieval is not None
    assert response.retrieval.requested_mode == "graph"


# --------------------------------------------------------------------------
# Staleness
# --------------------------------------------------------------------------


def test_a_graph_built_from_another_corpus_reports_itself_stale(
    corpus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index, _graph = corpus
    markdown_dir = Path(str(rag_service.MARKDOWN_OUTPUT_DIR))
    (markdown_dir / "extra.md").write_text(
        '---\ndocument_id: "extra"\ndocument_title: "Extra"\n---\n\n# Extra\n\n'
        "Optical benefits carry a waiting period of 13 weeks for new members.\n",
        encoding="utf-8",
    )
    rag_service.build_index(force=True)

    response = rag_service.answer_question("waiting period", mode="graph")
    assert response.retrieval is not None
    assert response.retrieval.graph_stale is True
