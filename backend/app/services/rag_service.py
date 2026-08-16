"""Local RAG over the converted Markdown knowledge base.

Design goals (kept deliberately simple):

- **No cloud search index.** The corpus is the Markdown this app already
  produces in ``data/local/markdown_outputs``. Retrieval is a pure-Python BM25
  pass over heading-aware chunks — no vector database, no extra dependencies.
- **Grounded generation only.** The Azure OpenAI chat deployment already
  configured for the converter writes the answer, but it may only use the
  retrieved passages. If retrieval finds nothing relevant we never call the
  model, so the app cannot invent a policy answer.
- **Structured answers** shaped after the PRD: a call-ready direct answer, key
  details, important notes/exceptions, expandable sources and a confidence
  indicator.

Two notes on retrieval, because the obvious approach is wrong here:

1. BM25 *scores* are used only for ranking. They are not a usable relevance
   gate, because IDF collapses when the corpus is small — with two chunks in the
   index every term is in "most" documents and every score is near zero. The
   gate is **query-term coverage** (what fraction of the question's meaningful
   terms the passage actually contains), which is independent of corpus size.
2. Terms are stemmed, and a query term that appears nowhere in the index is
   expanded by prefix, so "physio" still finds "physiotherapy".
"""
from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import AZURE_CHAT_DEPLOYMENT, FEEDBACK_LOG, MANIFEST_DIR, MARKDOWN_OUTPUT_DIR
from app.models import (
    ChatMessage,
    ChatResponse,
    ChatSource,
    KnowledgeDocument,
    KnowledgeStats,
    RetrievalReport,
)
from app.services.ai_plugin import _get_azure_client, _TokenTracker, is_plugin_available

# --------------------------------------------------------------------------
# Tuning knobs — all in one place so behaviour is easy to reason about.
# --------------------------------------------------------------------------
CHUNK_TARGET_CHARS = 1400
CHUNK_OVERLAP_CHARS = 200
TOP_K = 6
#: A passage must contain at least this fraction of the question's meaningful
#: terms to be treated as relevant. Below it we return the "cannot answer"
#: fallback rather than calling the model (PRD 3.8 — no hallucination).
MIN_COVERAGE = 0.34
#: Cap on prefix expansions for one unmatched query term.
MAX_PREFIX_EXPANSIONS = 6
MAX_CONTEXT_CHARS = 12000

_STOPWORDS = {
    "a", "about", "am", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "get", "got", "had", "has", "have",
    "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "much", "my", "no",
    "not", "of", "on", "or", "our", "please", "so", "tell", "than", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "to", "up", "us", "was", "we", "were",
    "what", "when", "where", "which", "who", "will", "with", "would", "you", "your",
}

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'&/-]*")


def _stem(word: str) -> str:
    """Very conservative suffix stripper — enough to unify plurals and tenses.

    Deliberately not a full Porter stemmer: this only needs "examinations" to
    meet "examination" and "therapies" to meet "therapy".
    """
    if len(word) <= 3:
        return word
    for suffix, replacement, min_stem in (
        ("ies", "y", 2),
        ("sses", "ss", 3),
        ("ing", "", 4),
        ("ed", "", 4),
        ("es", "", 4),
    ):
        if word.endswith(suffix) and len(word) - len(suffix) >= min_stem:
            return word[: -len(suffix)] + replacement
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _tokenize(text: str) -> list[str]:
    """Lowercase → drop stopwords → stem. Used for both corpus and queries."""
    return [
        _stem(token)
        for token in _WORD_RE.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 1
    ]


# --------------------------------------------------------------------------
# Index model
# --------------------------------------------------------------------------

@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    document_title: str
    heading: str
    text: str
    tokens: list[str] = field(default_factory=list)
    term_counts: Counter[str] = field(default_factory=Counter)

    @property
    def length(self) -> int:
        return len(self.tokens)


@dataclass
class Hit:
    """One retrieved passage."""
    chunk: Chunk
    score: float      #: BM25 — ranking only
    coverage: float   #: fraction of query terms present — relevance gate


@dataclass
class RetrievedPassage:
    """A passage that made it into the answer, whichever mode retrieved it.

    Both modes converge on this so context assembly, citation numbering and the
    source list are written once. The graph-only fields stay ``None`` in core
    mode rather than being faked, so a reader of the response can always tell
    which retriever produced it.
    """
    chunk: Chunk
    score: float
    coverage: float
    origin: str | None = None
    reason: str | None = None
    lexical: float | None = None
    graph: float | None = None
    hops: int | None = None

    @classmethod
    def from_hit(cls, hit: Hit) -> "RetrievedPassage":
        return cls(chunk=hit.chunk, score=hit.score, coverage=hit.coverage)


@dataclass
class IndexedDoc:
    document_id: str
    document_title: str
    source_filename: str
    source_type: str
    classification: str
    department: str | None
    access_group: str | None
    version: str | None
    converted_at_utc: str | None
    markdown_path: str
    char_count: int
    chunk_count: int


@dataclass
class KnowledgeIndex:
    docs: list[IndexedDoc]
    chunks: list[Chunk]
    doc_freq: Counter[str]
    vocab: tuple[str, ...]
    avg_chunk_length: float
    signature: tuple[tuple[str, float, int], ...]

    @property
    def is_empty(self) -> bool:
        return not self.chunks


_cached_index: KnowledgeIndex | None = None


# --------------------------------------------------------------------------
# Loading & chunking
# --------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Return ``(frontmatter, body)``. Frontmatter is the flat ``key: "value"`` block."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith(("  ", "-", "\t")):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"')
    return meta, raw[match.end():]


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Split a Markdown body into ``(heading, text)`` sections on ATX headings."""
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    for line in body.splitlines():
        if line.startswith("#") and line.lstrip("#").startswith(" "):
            if any(part.strip() for part in buffer):
                sections.append((heading, "\n".join(buffer).strip()))
            heading = line.lstrip("#").strip()
            buffer = []
        else:
            buffer.append(line)
    if any(part.strip() for part in buffer):
        sections.append((heading, "\n".join(buffer).strip()))
    return sections or [("", body.strip())]


def _window(text: str) -> list[str]:
    """Split an over-long section into overlapping windows on paragraph edges."""
    if len(text) <= CHUNK_TARGET_CHARS:
        return [text]
    windows: list[str] = []
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= CHUNK_TARGET_CHARS or not current:
            current = candidate
        else:
            windows.append(current)
            tail = current[-CHUNK_OVERLAP_CHARS:]
            current = f"{tail}\n\n{paragraph}".strip()
    if current:
        windows.append(current)
    return windows


def _chunk_document(document_id: str, title: str, body: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, text in _split_sections(body):
        for window in _window(text):
            if len(window.strip()) < 40:
                continue
            label = heading or title
            # Title and heading are indexed with the passage so "what does
            # <document> cover" retrieves the passage it names.
            tokens = _tokenize(f"{title}\n{label}\n{window}")
            if not tokens:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}#{len(chunks)}",
                    document_id=document_id,
                    document_title=title,
                    heading=label,
                    text=window.strip(),
                    tokens=tokens,
                    term_counts=Counter(tokens),
                )
            )
    return chunks


def _corpus_signature() -> tuple[tuple[str, float, int], ...]:
    """Cheap fingerprint of the corpus so the cache rebuilds when files change."""
    if not MARKDOWN_OUTPUT_DIR.exists():
        return ()
    entries: list[tuple[str, float, int]] = []
    for path in sorted(MARKDOWN_OUTPUT_DIR.glob("*.md")):
        if path.name.endswith(".raw.md"):
            continue
        stat = path.stat()
        entries.append((path.name, stat.st_mtime, stat.st_size))
    return tuple(entries)


def _read_manifest(document_id: str) -> dict[str, Any]:
    path = MANIFEST_DIR / f"{document_id}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def build_index(force: bool = False) -> KnowledgeIndex:
    """Build (or reuse) the in-memory index of every converted Markdown document."""
    global _cached_index

    signature = _corpus_signature()
    if not force and _cached_index is not None and _cached_index.signature == signature:
        return _cached_index

    docs: list[IndexedDoc] = []
    chunks: list[Chunk] = []

    paths = sorted(MARKDOWN_OUTPUT_DIR.glob("*.md")) if MARKDOWN_OUTPUT_DIR.exists() else []
    for path in paths:
        if path.name.endswith(".raw.md"):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue

        meta, body = _split_frontmatter(raw)
        manifest = _read_manifest(path.stem)
        document_id = meta.get("document_id") or manifest.get("document_id") or path.stem
        title = (
            meta.get("document_title")
            or manifest.get("document_title")
            or path.stem.replace("-", " ").title()
        )

        doc_chunks = _chunk_document(document_id, title, body)
        chunks.extend(doc_chunks)
        docs.append(
            IndexedDoc(
                document_id=document_id,
                document_title=title,
                source_filename=meta.get("source_filename") or manifest.get("source_filename") or path.name,
                source_type=meta.get("source_type") or manifest.get("source_type") or path.suffix.lstrip("."),
                classification=meta.get("classification") or manifest.get("classification") or "Internal",
                department=meta.get("department") or manifest.get("department"),
                access_group=meta.get("access_group") or manifest.get("access_group"),
                version=meta.get("version") or manifest.get("version"),
                converted_at_utc=meta.get("converted_at_utc") or manifest.get("converted_at_utc"),
                markdown_path=str(path),
                char_count=len(body),
                chunk_count=len(doc_chunks),
            )
        )

    doc_freq: Counter[str] = Counter()
    for chunk in chunks:
        doc_freq.update(set(chunk.tokens))
    avg_length = (sum(c.length for c in chunks) / len(chunks)) if chunks else 0.0

    _cached_index = KnowledgeIndex(
        docs=docs,
        chunks=chunks,
        doc_freq=doc_freq,
        vocab=tuple(sorted(doc_freq)),
        avg_chunk_length=avg_length,
        signature=signature,
    )
    return _cached_index


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

_BM25_K1 = 1.5
_BM25_B = 0.75


def _term_groups(query: str, idx: KnowledgeIndex) -> list[tuple[str, ...]]:
    """Turn a query into groups of index terms, one group per question term.

    A term present in the index becomes a single-member group. A term that is
    absent but long enough is expanded by prefix ("physio" → "physiotherapy",
    "physiotherapist"), so a group is the set of index terms that stand in for
    one thing the agent asked about. Terms with no match at all are dropped —
    they cannot contribute to coverage either way.
    """
    groups: list[tuple[str, ...]] = []
    for term in dict.fromkeys(_tokenize(query)):  # de-duplicate, keep order
        if idx.doc_freq.get(term):
            groups.append((term,))
        elif len(term) >= 5:
            expansions = tuple(
                candidate for candidate in idx.vocab if candidate.startswith(term)
            )[:MAX_PREFIX_EXPANSIONS]
            if expansions:
                groups.append(expansions)
    return groups


def search(query: str, top_k: int = TOP_K, index: KnowledgeIndex | None = None) -> list[Hit]:
    """Return up to ``top_k`` passages, ranked by BM25, each with its coverage."""
    idx = index or build_index()
    if idx.is_empty:
        return []

    groups = _term_groups(query, idx)
    if not groups:
        return []

    total_chunks = len(idx.chunks)
    hits: list[Hit] = []

    for chunk in idx.chunks:
        score = 0.0
        matched_groups = 0
        for group in groups:
            tf = sum(chunk.term_counts.get(term, 0) for term in group)
            if not tf:
                continue
            matched_groups += 1
            # Rarest member of the group carries the group's weight.
            df = min((idx.doc_freq.get(term, 0) or total_chunks) for term in group)
            idf = math.log(1 + (total_chunks - df + 0.5) / (df + 0.5))
            norm = 1 - _BM25_B + _BM25_B * (chunk.length / (idx.avg_chunk_length or 1))
            score += idf * (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * norm)
        if matched_groups:
            hits.append(
                Hit(
                    chunk=chunk,
                    score=round(score, 4),
                    coverage=round(matched_groups / len(groups), 4),
                )
            )

    # Coverage first, then BM25: a passage that answers more of the question
    # outranks one that merely repeats a single term often.
    hits.sort(key=lambda hit: (hit.coverage, hit.score), reverse=True)
    return hits[:top_k]


def _with_previous_question(question: str, history: list[ChatMessage]) -> str | None:
    """Prepend the previous question so a bare follow-up has something to match.

    "What's the limit?" carries no retrievable terms on its own. Returns ``None``
    when there is no earlier question to borrow from.

    This is only ever used as a *second* attempt (see ``answer_question``). Doing
    it up-front, gated on question length, misfires both ways: a self-contained
    question like "what is the refund for a dental examination?" has just three
    meaningful terms, and mixing the previous topic into it both dilutes coverage
    and drags in the wrong document.
    """
    previous = [message.content for message in history if message.role == "user"]
    if not previous:
        return None
    return f"{previous[-1]} {question}"


# --------------------------------------------------------------------------
# Answer generation
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are Knowledge Hub, the assistant used by customer-service agents while they are on a live call with a member.

Absolute rules:
1. Use ONLY the numbered CONTEXT passages below. They are the single source of truth.
2. Never invent or estimate benefits, limits, amounts, percentages, dates or conditions. If a figure is not in the context, do not state one.
3. If the context does not answer the question, set "answered" to false and leave the answer fields empty.
4. Write the direct answer so the agent can read it aloud: plain, warm, one or two short sentences.
5. Prefer policy-specific wording from the context over generic explanation.

Reply with ONLY a JSON object in exactly this shape:
{
  "answered": true | false,
  "direct_answer": "call-ready sentence(s)",
  "key_details": ["limits, coverage, eligibility - short bullets"],
  "important_notes": ["exclusions, conditions, pre-authorisation - short bullets"],
  "used_sources": [1, 2],
  "confidence": "High" | "Medium" | "Low",
  "follow_ups": ["up to 3 natural next questions the agent may need"]
}
Use [] for any list you have nothing for. Do not add commentary outside the JSON."""

_FALLBACK_ANSWER = (
    "I could not find this in the knowledge base, so I do not want to guess. "
    "Try rephrasing the question, or open the source document on the Documents tab "
    "and escalate if the call is urgent."
)


def _build_context(passages: list[RetrievedPassage]) -> tuple[str, list[ChatSource]]:
    blocks: list[str] = []
    sources: list[ChatSource] = []
    used = 0

    for position, passage in enumerate(passages, start=1):
        text = passage.chunk.text
        if used + len(text) > MAX_CONTEXT_CHARS:
            text = text[: max(0, MAX_CONTEXT_CHARS - used)]
        if not text.strip():
            break
        used += len(text)
        blocks.append(
            f"[{position}] Document: {passage.chunk.document_title}\n"
            f"Section: {passage.chunk.heading or '(document body)'}\n"
            f"{text}"
        )
        sources.append(
            ChatSource(
                index=position,
                chunk_id=passage.chunk.chunk_id,
                document_id=passage.chunk.document_id,
                document_title=passage.chunk.document_title,
                heading=passage.chunk.heading,
                excerpt=text.strip(),
                score=passage.score,
                cited=False,
                origin=passage.origin,
                retrieval_reason=passage.reason,
                lexical_score=passage.lexical,
                graph_score=passage.graph,
                hops=passage.hops,
            )
        )
    return "\n\n---\n\n".join(blocks), sources


# --------------------------------------------------------------------------
# Graph-expansion mode
# --------------------------------------------------------------------------

#: Modes the chat endpoint accepts.
CORE_MODE = "core"
GRAPH_MODE = "graph"


def _graph_retrieve(
    question: str,
    relevant: list[Hit],
    index: KnowledgeIndex,
    top_k: int,
) -> tuple[list[RetrievedPassage], dict[str, Any], bool] | None:
    """Expand the lexical hits over the compiled graph, or ``None`` if it cannot run.

    Imported lazily: ``graph_rag`` reads this module's tokeniser and ``Hit``
    type, so a module-level import here would be circular.
    """
    from app.services import graph_rag
    from app.services.graph_builder import corpus_fingerprint
    from app.services.graph_store import try_load_graph

    graph = try_load_graph()
    if graph is None:
        return None

    result = graph_rag.retrieve(question, relevant, graph, top_k)
    if not result.hits:
        return None

    by_id = {chunk.chunk_id: chunk for chunk in index.chunks}
    passages: list[RetrievedPassage] = []
    for hit in result.hits:
        chunk = by_id.get(hit.chunk_id)
        # A graph compiled from an older corpus can name a chunk that no longer
        # exists. Skipping it is right: the alternative is citing a passage
        # whose text we do not have.
        if chunk is None:
            continue
        passages.append(
            RetrievedPassage(
                chunk=chunk,
                score=hit.fused,
                coverage=hit.coverage,
                origin=hit.origin,
                reason=hit.reason,
                lexical=hit.lexical,
                graph=hit.graph,
                hops=hit.hops,
            )
        )

    if not passages:
        return None

    stale = graph.fingerprint != corpus_fingerprint(index)
    return passages, result.trace, stale


#: Low temperature: this is a factual lookup, not creative writing.
_ANSWER_TEMPERATURE = 0.2
_ANSWER_MAX_TOKENS = 1200


def _complete(tracker: Any, deployment: str, messages: list[dict[str, str]]) -> str:
    """One chat completion, preferring strict JSON mode.

    Not every deployment accepts ``response_format`` or a non-default
    temperature, so an argument rejection falls back to the plainest call the
    converter already relies on. Only rejections are retried — auth, quota and
    network errors propagate to the caller's degraded path.
    """
    attempts: list[dict[str, Any]] = [
        {
            "max_completion_tokens": _ANSWER_MAX_TOKENS,
            "temperature": _ANSWER_TEMPERATURE,
            "response_format": {"type": "json_object"},
        },
        {"max_completion_tokens": _ANSWER_MAX_TOKENS, "temperature": _ANSWER_TEMPERATURE},
        {"max_completion_tokens": _ANSWER_MAX_TOKENS, "temperature": 1},
    ]
    last_error: Exception | None = None
    for index, kwargs in enumerate(attempts):
        try:
            response = tracker.chat.completions.create(
                model=deployment, messages=messages, **kwargs
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            last_error = exc
            status = getattr(exc, "status_code", None)
            is_last = index == len(attempts) - 1
            # 400 == the deployment rejected an argument; anything else is real.
            if status != 400 or is_last:
                raise
    raise last_error if last_error else RuntimeError("chat completion failed")


def _parse_model_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON reply, tolerating code fences and stray prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"\A```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```\s*\Z", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            # Last resort: treat the whole reply as the direct answer.
            return {"answered": bool(cleaned), "direct_answer": cleaned, "confidence": "Low"}
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {"answered": bool(cleaned), "direct_answer": cleaned, "confidence": "Low"}
    return parsed if isinstance(parsed, dict) else {}


def _as_str_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit]


_CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _retrieval_confidence(hits: list[Hit]) -> str:
    """Confidence the retrieval itself justifies, from query-term coverage."""
    if not hits:
        return "Low"
    best = max(hit.coverage for hit in hits)
    if best >= 0.85:
        return "High"
    if best >= 0.55:
        return "Medium"
    return "Low"


def _passage_confidence(passages: list[RetrievedPassage]) -> str:
    """Confidence the retrieved set justifies, whichever mode produced it.

    Still driven by query-term coverage, and still measured on the passages that
    matched the question lexically. A graph-expanded passage has no coverage of
    its own — it was reached by association — so counting it would let a wide
    walk inflate confidence in an answer no passage directly supports.
    """
    covered = [passage.coverage for passage in passages if passage.origin != "expanded"]
    if not covered:
        return "Low"
    best = max(covered)
    if best >= 0.85:
        return "High"
    if best >= 0.55:
        return "Medium"
    return "Low"


def _normalise_confidence(model_value: Any, retrieval_value: str) -> str:
    """Never report higher confidence than the retrieved evidence supports."""
    claimed = str(model_value or "").strip().title()
    if claimed not in _CONFIDENCE_RANK:
        return retrieval_value
    return claimed if _CONFIDENCE_RANK[claimed] <= _CONFIDENCE_RANK[retrieval_value] else retrieval_value


def answer_question(
    question: str,
    history: list[ChatMessage] | None = None,
    top_k: int = TOP_K,
    mode: str = GRAPH_MODE,
) -> ChatResponse:
    """Answer ``question`` using only the local knowledge base.

    ``mode`` selects the retriever. ``core`` is BM25 alone; ``graph`` seeds with
    BM25 and then expands over the compiled knowledge graph. Both answer from
    retrieved text only — the mode changes which passages are put in front of
    the model, never whether the model is allowed to go beyond them.
    """
    started = time.perf_counter()
    history = history or []
    index = build_index()

    requested_mode = mode if mode in (CORE_MODE, GRAPH_MODE) else GRAPH_MODE

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    if index.is_empty:
        return ChatResponse(
            answered=False,
            direct_answer=(
                "The knowledge base is empty. Convert a document on the Documents tab "
                "first, then I can answer questions about it."
            ),
            confidence="Low",
            fallback_reason="no_documents_indexed",
            latency_ms=elapsed_ms(),
        )

    # Pass 1: the question as asked, so coverage measures exactly what was asked.
    hits = search(question, top_k=max(top_k, TOP_K), index=index)
    relevant = [hit for hit in hits if hit.coverage >= MIN_COVERAGE]

    # Pass 2: only if that found nothing, retry carrying the previous question —
    # this is what rescues bare follow-ups without contaminating standalone ones.
    if not relevant:
        carried = _with_previous_question(question, history)
        if carried is not None:
            hits = search(carried, top_k=max(top_k, TOP_K), index=index)
            relevant = [hit for hit in hits if hit.coverage >= MIN_COVERAGE]

    # The gate is checked on the lexical pass, before any expansion.
    #
    # This is the load-bearing decision in graph mode. Expansion starts from the
    # seeds, so if nothing lexical is relevant the walk begins nowhere and every
    # passage it reaches is reached by association alone. Letting that produce
    # context would hand the model plausible, unrelated policy text and invite
    # precisely the confident wrong answer the coverage gate exists to prevent.
    # The graph widens the evidence behind an answer; it never creates one.
    if not relevant:
        return ChatResponse(
            answered=False,
            direct_answer=_FALLBACK_ANSWER,
            confidence="Low",
            fallback_reason="no_relevant_passages",
            suggested_topics=[doc.document_title for doc in index.docs[:5]],
            latency_ms=elapsed_ms(),
            retrieval=RetrievalReport(
                mode=CORE_MODE,
                requested_mode=requested_mode,
                fell_back=requested_mode == GRAPH_MODE,
                fallback_reason="no_relevant_passages" if requested_mode == GRAPH_MODE else None,
            ),
        )

    # ── Retrieval ───────────────────────────────────────────────────────────
    passages: list[RetrievedPassage] = []
    report = RetrievalReport(mode=CORE_MODE, requested_mode=requested_mode, seed_count=len(relevant))

    if requested_mode == GRAPH_MODE:
        expanded = _graph_retrieve(question, relevant, index, top_k)
        if expanded is None:
            report.fell_back = True
            report.fallback_reason = "no_graph_snapshot"
        else:
            passages, trace, stale = expanded
            counts = trace.get("counts", {})
            report = RetrievalReport(
                mode=GRAPH_MODE,
                requested_mode=requested_mode,
                seed_count=int(counts.get("seed_chunks", len(relevant))),
                expanded_count=int(counts.get("selected_expanded", 0)),
                linked_concepts=[item["label"] for item in trace.get("linked_concepts", [])],
                nodes_reached=int(counts.get("nodes_reached", 0)),
                edges_traversed=int(counts.get("edges_traversed", 0)),
                highlight_nodes=list(trace.get("highlight_nodes", [])),
                highlight_edges=list(trace.get("highlight_edges", [])),
                settings=dict(trace.get("settings", {})),
                graph_stale=stale,
            )

    if not passages:
        passages = [RetrievedPassage.from_hit(hit) for hit in relevant[:top_k]]

    retrieval_ms = int((time.perf_counter() - started) * 1000)
    context, sources = _build_context(passages)

    if not is_plugin_available():
        # Still useful without a model: show the passages we found, verbatim.
        for source in sources:
            source.cited = True
        return ChatResponse(
            answered=False,
            direct_answer=(
                "Azure OpenAI is not configured, so I cannot write an answer. The most "
                "relevant passages from the knowledge base are listed under Sources."
            ),
            confidence="Low",
            fallback_reason="model_not_configured",
            sources=sources,
            latency_ms=elapsed_ms(),
            retrieval_ms=retrieval_ms,
            retrieval=report,
        )

    deployment = AZURE_CHAT_DEPLOYMENT or "gpt-chat-latest"
    tracker = _TokenTracker(_get_azure_client(), deployment)

    messages: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for turn in history[-6:]:  # session context for follow-up questions (PRD 3.1)
        if turn.role in ("user", "assistant") and turn.content.strip():
            messages.append({"role": turn.role, "content": turn.content.strip()})
    messages.append(
        {"role": "user", "content": f"CONTEXT:\n{context}\n\n---\n\nAGENT QUESTION: {question}"}
    )

    try:
        raw = _complete(tracker, deployment, messages)
    except Exception as exc:  # network / auth / quota — degrade, never break the chat
        for source in sources:
            source.cited = True
        return ChatResponse(
            answered=False,
            direct_answer=(
                "I could not reach the AI service just now. The most relevant knowledge-base "
                "passages are listed under Sources so you can read them directly."
            ),
            confidence="Low",
            fallback_reason=f"model_error: {exc}",
            sources=sources,
            latency_ms=elapsed_ms(),
            retrieval_ms=retrieval_ms,
            retrieval=report,
            model=deployment,
        )

    parsed = _parse_model_json(raw)
    direct_answer = str(parsed.get("direct_answer") or "").strip()
    answered = bool(parsed.get("answered", False)) and bool(direct_answer)
    cost = tracker.build_cost_estimate(["Knowledge Hub chat answer"])

    cited_positions = {
        int(value)
        for value in (parsed.get("used_sources") or [])
        if str(value).strip().lstrip("-").isdigit()
    }
    for source in sources:
        source.cited = source.index in cited_positions if cited_positions else answered

    if not answered:
        return ChatResponse(
            answered=False,
            direct_answer=direct_answer or _FALLBACK_ANSWER,
            confidence="Low",
            fallback_reason="model_reported_no_grounded_answer",
            sources=sources,
            suggested_topics=[doc.document_title for doc in index.docs[:5]],
            latency_ms=elapsed_ms(),
            retrieval_ms=retrieval_ms,
            retrieval=report,
            model=deployment,
            ai_cost=cost,
        )

    return ChatResponse(
        answered=True,
        direct_answer=direct_answer,
        key_details=_as_str_list(parsed.get("key_details"), 8),
        important_notes=_as_str_list(parsed.get("important_notes"), 8),
        confidence=_normalise_confidence(parsed.get("confidence"), _passage_confidence(passages)),
        sources=sources,
        follow_ups=_as_str_list(parsed.get("follow_ups"), 3),
        latency_ms=elapsed_ms(),
        retrieval_ms=retrieval_ms,
        retrieval=report,
        model=deployment,
        ai_cost=cost,
    )


# --------------------------------------------------------------------------
# Knowledge-base browsing helpers
# --------------------------------------------------------------------------

def list_documents() -> list[KnowledgeDocument]:
    return [
        KnowledgeDocument(
            document_id=doc.document_id,
            document_title=doc.document_title,
            source_filename=doc.source_filename,
            source_type=doc.source_type,
            classification=doc.classification,
            department=doc.department,
            access_group=doc.access_group,
            version=doc.version,
            converted_at_utc=doc.converted_at_utc,
            char_count=doc.char_count,
            chunk_count=doc.chunk_count,
        )
        for doc in build_index().docs
    ]


def knowledge_stats() -> KnowledgeStats:
    index = build_index()
    return KnowledgeStats(
        document_count=len(index.docs),
        chunk_count=len(index.chunks),
        model=AZURE_CHAT_DEPLOYMENT or "gpt-chat-latest",
        model_configured=is_plugin_available(),
    )


#: Question shapes, most specific first, keyed to the words a section heading
#: uses. Every shape names its document, because a corpus of near-identical
#: schedules answers "what is excluded?" nine different ways and the starter
#: prompt has to pick one. ``section`` is the catch-all for a heading that names
#: a topic rather than a kind of clause.
_SUGGESTION_SHAPES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("waiting", ("waiting",), "What waiting periods apply under {title}?"),
    ("exclusions", ("exclusion", "excluded", "not cover"), "What is not covered by {title}?"),
    ("limits", ("limit", "cap "), "What annual limits apply under {title}?"),
    ("claiming", ("claim", "submission", "submit"), "How is a claim submitted under {title}?"),
    ("pre_auth", ("pre-auth", "authorisation"), "When is pre-authorisation required under {title}?"),
    ("section", (), "What does {title} say about {heading}?"),
)


def _document_suggestions(title: str, headings: list[str]) -> list[tuple[str, str]]:
    """Every starter prompt one document can offer, as ``(shape, question)``.

    Ordered by ``_SUGGESTION_SHAPES``, so the caller can ask this document for
    its best unused kind of question rather than whatever came first.
    """
    if not headings:
        return [("document", f"What does {title} cover?")]

    offers: list[tuple[str, str]] = []
    for shape, keywords, template in _SUGGESTION_SHAPES:
        for heading in headings:
            lowered = heading.lower()
            if keywords and not any(keyword in lowered for keyword in keywords):
                continue
            offers.append((shape, template.format(title=title, heading=heading)))
            break
    return offers


def suggested_questions(limit: int = 6) -> list[str]:
    """Starter prompts derived from the corpus, so they always retrieve something.

    Two things make a starter prompt useful and neither is the wording: it has
    to retrieve, and the set has to show the reader what the knowledge base
    holds. So each prompt is built from a real section heading in a real
    document and carries that document's title — the terms are guaranteed to be
    in the index — and the set is spread one prompt per document, each asking a
    different kind of question. Four prompts drawn from one schedule would
    demonstrate a quarter of the corpus and teach the agent nothing.
    """
    index = build_index()

    headings_by_doc: dict[str, list[str]] = {}
    for chunk in index.chunks:
        if not chunk.heading or chunk.heading == chunk.document_title or len(chunk.heading) > 60:
            continue
        seen_headings = headings_by_doc.setdefault(chunk.document_id, [])
        if chunk.heading not in seen_headings:
            seen_headings.append(chunk.heading)

    offers_by_doc = {
        doc.document_id: _document_suggestions(
            doc.document_title, headings_by_doc.get(doc.document_id, [])
        )
        for doc in index.docs
    }

    questions: list[str] = []
    used_shapes: set[str] = set()
    # One pass per document before any document is asked twice, so a corpus of
    # nine schedules is represented by nine before it is represented by two.
    while len(questions) < limit and offers_by_doc:
        progressed = False
        for doc in index.docs:
            if len(questions) >= limit:
                break
            offers = [
                (shape, question)
                for shape, question in offers_by_doc.get(doc.document_id, [])
                if question not in questions
            ]
            if not offers:
                continue
            # A kind of question nobody has asked yet, or this document's best.
            shape, question = next(
                (offer for offer in offers if offer[0] not in used_shapes), offers[0]
            )
            questions.append(question)
            used_shapes.add(shape)
            progressed = True
        if not progressed:
            break
    return questions


def record_feedback(payload: dict[str, Any]) -> None:
    """Append one feedback event to the local JSONL log (PRD 3.7)."""
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
