"""Retrieval tests for the Knowledge Hub chat.

These cover the parts that decide whether the assistant answers at all — the
chunker, the stemmer, prefix expansion and the coverage gate. None of them call
Azure OpenAI, so the whole file runs offline.
"""
from collections import Counter

from app.services import rag_service as rag


def _index_from(documents: dict[str, str]) -> rag.KnowledgeIndex:
    """Build an index in memory from ``{title: body}`` without touching disk."""
    chunks: list[rag.Chunk] = []
    docs: list[rag.IndexedDoc] = []
    for position, (title, body) in enumerate(documents.items()):
        document_id = f"doc-{position}"
        doc_chunks = rag._chunk_document(document_id, title, body)
        chunks.extend(doc_chunks)
        docs.append(
            rag.IndexedDoc(
                document_id=document_id,
                document_title=title,
                source_filename=f"{document_id}.pdf",
                source_type="PDF",
                classification="Internal",
                department=None,
                access_group=None,
                version=None,
                converted_at_utc=None,
                markdown_path=f"{document_id}.md",
                char_count=len(body),
                chunk_count=len(doc_chunks),
            )
        )
    doc_freq: Counter[str] = Counter()
    for chunk in chunks:
        doc_freq.update(set(chunk.tokens))
    return rag.KnowledgeIndex(
        docs=docs,
        chunks=chunks,
        doc_freq=doc_freq,
        vocab=tuple(sorted(doc_freq)),
        avg_chunk_length=(sum(c.length for c in chunks) / len(chunks)) if chunks else 0.0,
        signature=(),
    )


SAMPLE = {
    "Dental Protect Plus": (
        "# Dental Protect Plus\n\n"
        "Examinations: 100% refund - 2 per year.\n\n"
        "Scaling and polishing: 100% refund - 2 per year.\n\n"
        "Panoramic X-ray: 100% refund - 1 every 5 years.\n"
    ),
    "Physiotherapy Benefits": (
        "# Physiotherapy Benefits\n\n"
        "Physiotherapy: 75% refund, up to 10 visits per policy year. "
        "The therapist must be registered with the ISCP.\n\n"
        "Receipts must be submitted within 12 months of the contract end date.\n"
    ),
}


def test_stemmer_unifies_plurals_and_tenses() -> None:
    assert rag._stem("examinations") == rag._stem("examination")
    assert rag._stem("therapies") == "therapy"
    assert rag._stem("refunded") == "refund"
    assert rag._stem("visits") == "visit"
    # Must not mangle words that merely end in s.
    assert rag._stem("iscp") == "iscp"


def test_stopwords_do_not_count_towards_coverage() -> None:
    # "What is the ..." contributes nothing; only "dental" and "cover" do.
    assert rag._tokenize("What is the dental cover?") == ["dental", "cover"]


def test_relevant_question_is_retrieved_with_full_coverage() -> None:
    index = _index_from(SAMPLE)
    hits = rag.search("What is the refund for a dental examination?", index=index)
    assert hits, "expected at least one passage"
    assert hits[0].coverage >= rag.MIN_COVERAGE
    assert hits[0].chunk.document_title == "Dental Protect Plus"


def test_out_of_corpus_question_is_rejected() -> None:
    index = _index_from(SAMPLE)
    for question in ("what is the capital of france", "how do I renew my car insurance"):
        hits = rag.search(question, index=index)
        relevant = [hit for hit in hits if hit.coverage >= rag.MIN_COVERAGE]
        assert not relevant, f"{question!r} should not match the corpus"


def test_prefix_expansion_finds_longer_term() -> None:
    """'physio' is not in the corpus; 'physiotherapy' is."""
    index = _index_from(SAMPLE)
    groups = rag._term_groups("physio cover", index)
    expanded = [group for group in groups if any(term.startswith("physio") for term in group)]
    assert expanded, "expected 'physio' to expand by prefix"

    hits = rag.search("physio refund", index=index)
    assert hits
    assert hits[0].chunk.document_title == "Physiotherapy Benefits"


def test_coverage_ranks_above_raw_term_frequency() -> None:
    """A passage answering more of the question wins, even on a small corpus."""
    index = _index_from(SAMPLE)
    hits = rag.search("physiotherapy ISCP registered therapist", index=index)
    assert hits[0].chunk.document_title == "Physiotherapy Benefits"
    assert hits[0].coverage > 0.5


def test_standalone_question_retrieves_without_help() -> None:
    """A self-contained question must pass the gate on its own terms.

    "What is the refund for a dental examination?" has only three meaningful
    terms, so any length-based follow-up heuristic would wrongly rewrite it.
    """
    index = _index_from(SAMPLE)
    hits = rag.search("What is the refund for a dental examination?", index=index)
    relevant = [hit for hit in hits if hit.coverage >= rag.MIN_COVERAGE]
    assert relevant
    assert relevant[0].chunk.document_title == "Dental Protect Plus"


def test_bare_follow_up_needs_the_previous_question() -> None:
    from app.models import ChatMessage

    index = _index_from(SAMPLE)
    history = [ChatMessage(role="user", content="How am I covered for physiotherapy?")]

    # On its own, "what is the limit?" has nothing the corpus can match.
    alone = [hit for hit in rag.search("what is the limit?", index=index) if hit.coverage >= rag.MIN_COVERAGE]

    carried = rag._with_previous_question("what is the limit?", history)
    assert carried is not None and "physiotherapy" in carried
    rescued = [hit for hit in rag.search(carried, index=index) if hit.coverage >= rag.MIN_COVERAGE]
    assert rescued, "carrying the previous question should rescue a bare follow-up"
    assert len(rescued) >= len(alone)
    assert rescued[0].chunk.document_title == "Physiotherapy Benefits"


def test_no_history_means_nothing_to_carry() -> None:
    assert rag._with_previous_question("what is the limit?", []) is None


def test_confidence_never_exceeds_retrieval_evidence() -> None:
    # The model claiming "High" on a weak retrieval is downgraded.
    assert rag._normalise_confidence("High", "Low") == "Low"
    assert rag._normalise_confidence("High", "Medium") == "Medium"
    # A more cautious model claim is respected.
    assert rag._normalise_confidence("Low", "High") == "Low"
    # Garbage falls back to what retrieval supports.
    assert rag._normalise_confidence("banana", "Medium") == "Medium"


def test_model_json_parsing_tolerates_fences_and_prose() -> None:
    fenced = '```json\n{"answered": true, "direct_answer": "Yes."}\n```'
    assert rag._parse_model_json(fenced)["direct_answer"] == "Yes."

    chatty = 'Sure! {"answered": false, "direct_answer": "No."} Hope that helps.'
    assert rag._parse_model_json(chatty)["answered"] is False

    # Non-JSON must not raise; it degrades to a low-confidence answer.
    assert rag._parse_model_json("plain text")["confidence"] == "Low"


def test_frontmatter_is_stripped_from_indexed_body() -> None:
    raw = '---\ndocument_id: "abc"\ndocument_title: "Test Doc"\n---\n\n# Test Doc\n\nBody text here.\n'
    meta, body = rag._split_frontmatter(raw)
    assert meta["document_id"] == "abc"
    assert meta["document_title"] == "Test Doc"
    assert "document_id" not in body
    assert "Body text here." in body


SCHEDULES = {
    "Dental Complete": (
        "# Dental Complete\n\n"
        "## Major Restorative Treatment\n\n"
        "Root canal treatment is refunded at 70%, one per tooth every five years.\n\n"
        "## Exclusions\n\n"
        "Implants, veneers and cosmetic whitening are excluded.\n\n"
        "## Claiming Under This Schedule\n\n"
        "Submit the dentist's receipt through the member portal.\n"
    ),
    "Hospital Cover": (
        "# Hospital Cover\n\n"
        "## Waiting Periods\n\n"
        "A 26-week waiting period applies to new members.\n\n"
        "## Exclusions\n\n"
        "Cosmetic surgery is excluded on every plan.\n"
    ),
}


def _starter_prompts(monkeypatch, documents: dict[str, str], limit: int = 4) -> tuple[list[str], rag.KnowledgeIndex]:
    index = _index_from(documents)
    monkeypatch.setattr(rag, "build_index", lambda *args, **kwargs: index)
    return rag.suggested_questions(limit=limit), index


def test_starter_prompts_cover_every_document_before_repeating_one(monkeypatch) -> None:
    questions, _ = _starter_prompts(monkeypatch, SCHEDULES, limit=2)
    assert len(questions) == 2
    # Two schedules, two prompts: one each, rather than two from the first.
    assert any("Dental Complete" in question for question in questions)
    assert any("Hospital Cover" in question for question in questions)


def test_starter_prompts_ask_different_kinds_of_question(monkeypatch) -> None:
    questions, _ = _starter_prompts(monkeypatch, SCHEDULES, limit=4)
    assert len(questions) == len(set(questions))
    # Four prompts that all begin "What is not covered by…" would demonstrate
    # one capability four times.
    openings = {" ".join(question.split()[:3]) for question in questions}
    assert len(openings) > 1


def test_every_starter_prompt_retrieves_the_document_it_names(monkeypatch) -> None:
    questions, index = _starter_prompts(monkeypatch, SCHEDULES, limit=4)
    assert questions
    for question in questions:
        named = next(title for title in SCHEDULES if title in question)
        hits = [hit for hit in rag.search(question, index=index) if hit.coverage >= rag.MIN_COVERAGE]
        assert hits, f"starter prompt retrieves nothing: {question}"
        assert any(hit.chunk.document_title == named for hit in hits[:3]), (
            f"starter prompt does not retrieve the document it names: {question}"
        )


def test_no_documents_means_no_starter_prompts(monkeypatch) -> None:
    questions, _ = _starter_prompts(monkeypatch, {}, limit=4)
    assert questions == []
