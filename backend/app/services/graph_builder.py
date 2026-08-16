"""Compile the converted Markdown corpus into a knowledge graph.

This is the structure the graph view draws and the graph-expansion retriever
walks. It is derived entirely from the corpus the converter already produced —
there is no hand-authored ontology, no supplied vocabulary and no model call
anywhere in this module. Given the same Markdown it produces the same graph, byte for byte,
which is what makes a retrieval result reproducible six months after the fact.

Node types
----------
``document``    one converted file, carrying its governance metadata
``section``     one ATX heading and the body under it
``chunk``       one retrieval unit — the passage the retriever actually scores
``concept``     a multi-word domain term mined from the corpus
``value``       a money amount or a percentage
``constraint``  a frequency limit, waiting period or age bound
``community``   a cluster of concepts that keep occurring together
``facet``       a governance value: department, classification, access group

Relations
---------
``contains``     document → section        the document's outline
``has_chunk``    section  → chunk          which passages came from which section
``follows``      chunk    → chunk          reading order inside a document
``mentions``     chunk    → concept/value/constraint
``co_occurs``    concept  ↔ concept        significant co-mention, ranked by NPMI
``in_community`` concept  → community
``similar_to``   chunk    ↔ chunk          TF-IDF cosine, mostly cross-document
``governed_by``  document → facet

Why these and not others: every one of them is a fact already present in the
corpus. ``contains`` and ``follows`` come from the file's own structure,
``mentions`` from term extraction, ``similar_to`` from the vector space the
retriever already builds, ``co_occurs`` and ``in_community`` from counting.
Nothing is asserted that the documents do not already say.

Three techniques do the work, all standard and all deterministic:

1. **C-value term extraction** (Frantzi, Ananiadou & Mima, 2000) mines the
   concept vocabulary. It favours multi-word terms and discounts a short term
   that mostly appears inside a longer one, so "restorative treatment" survives
   and the bare "treatment" that only ever appears inside it does not.
2. **Normalised pointwise mutual information** ranks concept pairs, so
   ``co_occurs`` records association rather than mere frequency. Without it
   every common term links to every other common term and the graph says
   nothing.
3. **Label propagation** (Raghavan, Albert & Kumara, 2007) finds concept
   communities. Near-linear, no parameter to tune, and made deterministic here
   by processing nodes in a fixed order and breaking label ties
   lexicographically.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.rag_service import (
    Chunk,
    KnowledgeIndex,
    IndexedDoc,
    _STOPWORDS,
    _stem,
    build_index,
)

SCHEMA_VERSION = "2.0"

# --------------------------------------------------------------------------
# Tuning. Every threshold that shapes the graph lives here, so the write-up can
# quote one table and the reader can find each number in one place.
# --------------------------------------------------------------------------

#: Longest concept phrase mined. Four covers "major restorative dental treatment";
#: beyond that candidates are almost always sentence fragments.
MAX_TERM_WORDS = 4
#: A concept must appear in at least this many distinct chunks. One-off phrases
#: cannot connect anything, and they are what makes a mined vocabulary noisy.
MIN_CONCEPT_CHUNK_FREQ = 2
#: Hard ceiling on the mined vocabulary, taken by C-value.
MAX_CONCEPTS = 240
#: Concepts kept per chunk in the corpus, below the hard ceiling.
#:
#: The vocabulary a corpus supports scales with how much text there is. A fixed
#: 240 is right for a few hundred chunks and far too many for sixty: every term
#: past the useful ones still gets a node and a fistful of `mentions` edges, and
#: the document → section → chunk backbone vanishes under them. Scaling the cap
#: keeps the graph legible on a small corpus without capping a large one.
CONCEPTS_PER_CHUNK = 2.5
MIN_CONCEPTS = 60
#: Entities recorded per chunk, ranked by TF-IDF salience inside that chunk.
#: Uncapped, `mentions` alone runs to tens of thousands of edges and the useful
#: structure disappears underneath it. Because salience is TF-IDF, the ones that
#: survive the cut are the specific terms, which are also the ones worth
#: expanding along.
MAX_MENTIONS_PER_CHUNK = 10
#: Co-occurrence edges kept per concept, ranked by NPMI. This is the knob that
#: sets how coarse the communities come out: too low and the concept graph
#: breaks into dozens of disconnected fragments that label propagation reports
#: as "communities" of five or six terms each.
MAX_CO_OCCURS_PER_CONCEPT = 10
#: A concept pair must be seen together this often before NPMI is trusted.
MIN_CO_OCCURRENCE = 2
#: Nearest neighbours kept per chunk, and the cosine below which a neighbour is
#: not worth an edge.
MAX_SIMILAR_PER_CHUNK = 3
MIN_SIMILARITY = 0.14
#: Label propagation sweeps. It converges on a graph this size in far fewer;
#: the cap only stops a pathological oscillation.
LABEL_PROPAGATION_ROUNDS = 30
#: A community smaller than this is dissolved back into unclustered concepts —
#: a "community" of one is a node with a hat on.
MIN_COMMUNITY_SIZE = 3


# --------------------------------------------------------------------------
# Pattern entities
#
# These are the figures an agent is actually asked for on a call — "how much",
# "how often", "from what age" — and they are the part of a benefit document
# that a purely lexical retriever is worst at, because the number carries the
# meaning and the number is not in the question. Extracting them as nodes is
# what lets the graph connect "1 per surface every 2 years" to the treatment it
# governs even though the two sit in different table cells.
# --------------------------------------------------------------------------

_MONEY_RE = re.compile(
    r"(?:€\s?\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*(?:\.\d{2})?\s*euro\b)",
    re.IGNORECASE,
)
_RATE_RE = re.compile(r"\b\d{1,3}\s?%(?:\s+refund)?", re.IGNORECASE)

_CONSTRAINT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "frequency",
        re.compile(
            r"\b\d+\s+per\s+(?:year|pregnancy|birth|eye|ear|hand|joint|tooth|surface|lifetime|course|visit|member)\b",
            re.IGNORECASE,
        ),
    ),
    ("frequency", re.compile(r"\b(?:once|twice)\s+per\s+[\w-]+\s+period\b", re.IGNORECASE)),
    ("frequency", re.compile(r"\b\d+\s+every\s+\d+\s+years?\b", re.IGNORECASE)),
    ("frequency", re.compile(r"\bevery\s+\d+\s+years?\b", re.IGNORECASE)),
    ("frequency", re.compile(r"\bno\s+(?:annual|frequency)\s+limit\b", re.IGNORECASE)),
    (
        "waiting_period",
        re.compile(r"\bwaiting\s+period\s+of\s+\d+\s+(?:weeks?|months?|days?)\b", re.IGNORECASE),
    ),
    ("duration", re.compile(r"\bup\s+to\s+\d+\s+days?\s+per\s+year\b", re.IGNORECASE)),
    ("duration", re.compile(r"\bwithin\s+\d+\s+(?:working\s+days?|hours?|weeks?|months?)\b", re.IGNORECASE)),
    ("age", re.compile(r"\b(?:up\s+to|under|over)\s+the\s+age\s+of\s+\d+\b", re.IGNORECASE)),
    ("age", re.compile(r"\baged\s+\d+(?:\s*(?:&|and)\s*above)?\b", re.IGNORECASE)),
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'&/-]*")

#: A phrase may not span these.
#:
#: This matters more than it looks. The converter renders tables as Markdown
#: pipe rows, so "| Scaling and polishing | 80% refund | 1 per year |" is one
#: line of text. Mining n-grams straight off it invents phrases that cross cell
#: boundaries — "polishing refund per year" — which are not terms in the corpus,
#: are not terms in any document, and are indistinguishable from real ones once
#: they are in the graph. Segmenting on cell and clause boundaries first is what
#: keeps the mined vocabulary honest.
_SEGMENT_RE = re.compile(r"[|\n\r.;:!?()\[\]]+")

#: Function words. No token of a mined phrase may be one of these.
#:
#: Term extraction normally applies a part-of-speech filter here, keeping only
#: noun-phrase shapes. There is no tagger in this project and adding one would
#: put a model in the middle of a step that is otherwise pure counting, so the
#: filter is approximated with a stoplist: articles, prepositions, conjunctions,
#: pronouns, auxiliaries and modals. Domain terms in this corpus are plain noun
#: phrases — "waiting period", "frequency limit", "consultant referral" — so the
#: approximation costs very little and keeps the stage deterministic.
_FUNCTION_WORDS = _STOPWORDS | {
    # prepositions and connectives
    "per", "up", "under", "over", "within", "before", "after", "during", "since",
    "against", "through", "between", "above", "below", "out", "off", "down",
    "without", "into", "onto", "upon", "unless", "except", "toward", "towards",
    "across", "along", "around", "behind", "beyond", "despite", "throughout",
    "each", "every", "both", "either", "neither", "also", "only", "same", "other",
    "such", "more", "most", "less", "least", "own", "very", "still", "already",
    "however", "where", "while", "whether", "because", "again", "further",
    "once", "twice", "here", "now", "then", "all", "some", "none", "than",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "first", "second", "third", "next", "last", "another", "many", "few",
    # auxiliaries and modals
    "is", "are", "was", "were", "been", "being", "am", "shall", "should", "must",
    "may", "might", "will", "would", "can", "could", "does", "did", "do", "done",
    "having", "not", "no", "yes", "any",
    # currency words: amounts are captured as `value` nodes, so the unit itself
    # is not a concept and only ever appears because a figure preceded it.
    "euro", "eur", "cent", "cents",
}

#: Verb and adjective forms that survive the function-word filter but never head
#: a noun-phrase term.
#:
#: Written as surface words and stemmed at import rather than as hand-written
#: stems. Guessing the stemmer's output by eye is how "applies" slipped through
#: a blocklist that already contained "appli" — the stemmer maps it to "apply".
#: Deliberately narrow: "cover", "claim", "refund", "limit" and "schedule" are
#: nouns in this domain and are absent from this list even though each also
#: occurs as a verb.
_VERBAL_SURFACE_FORMS = {
    "apply", "applies", "applied", "require", "requires", "required",
    "submit", "submits", "submitted", "exclude", "excludes", "excluded",
    "assess", "assessed", "confirm", "confirmed", "notify", "notified",
    "authorise", "authorised", "retrospectively", "automatically",
    "individually", "separately", "receive", "received", "calculate",
    "calculated", "deduct", "deducted", "release", "released", "reach",
    "reaches", "arrive", "arrives", "decide", "decided", "begin", "begun",
    "give", "given", "take", "taken", "make", "made", "carry", "carries",
    "include", "includes", "including", "provide", "provides", "provided",
    "show", "shows", "list", "listed", "read", "hold", "held", "keep",
    "stop", "change", "changed", "review", "reviewed", "mean", "means",
    "run", "runs", "want", "need", "needs", "pay", "pays", "paid",
    "effective", "available", "claimable", "appealable", "payable",
    "immediate", "further", "relevant", "standard", "additional",
}
_VERBAL_STEMS = {_stem(word) for word in _VERBAL_SURFACE_FORMS}


@dataclass
class _Candidate:
    """One mined phrase, before C-value scoring decides whether it survives."""

    words: int
    total_occurrences: int = 0
    chunk_ids: set[str] = field(default_factory=set)
    #: Every surface form seen for this stem key, so the label can be the one
    #: the corpus actually favours rather than whichever was read first —
    #: otherwise the "limit" concept displays as "limits" on the strength of one
    #: early plural.
    surfaces: Counter[str] = field(default_factory=Counter)
    #: Occurrences that sit inside a longer candidate — the C-value discount.
    nested_occurrences: int = 0
    nested_in: int = 0

    @property
    def chunk_frequency(self) -> int:
        return len(self.chunk_ids)

    @property
    def surface(self) -> str:
        """The most frequent surface form; shortest wins a tie, then alphabetical."""
        if not self.surfaces:
            return ""
        return min(self.surfaces.items(), key=lambda item: (-item[1], len(item[0]), item[0]))[0]


# --------------------------------------------------------------------------
# Term extraction
# --------------------------------------------------------------------------


def _segments(text: str) -> list[list[str]]:
    """Split text into word runs that a phrase is allowed to span.

    One list per table cell, clause or sentence. Nothing crosses a boundary.
    """
    return [
        words
        for fragment in _SEGMENT_RE.split(text)
        if (words := _WORD_RE.findall(fragment))
    ]


def _surface_words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _normalise_phrase(words: Iterable[str]) -> str:
    """Stem-join a phrase so "frequency limits" and "frequency limit" are one term."""
    return " ".join(_stem(word.lower()) for word in words)


def _is_valid_phrase(words: list[str]) -> bool:
    """The linguistic filter: a candidate must look like a noun phrase.

    Every token has to be a content word, not just the first and last. A rule
    that only guards the edges lets "refund per surface" and "claim must reach"
    through, and those read exactly like real terms once they are nodes.
    """
    if not words:
        return False
    lowered = [word.lower() for word in words]
    if any(len(word) < 2 for word in lowered):
        return False
    if any(word in _FUNCTION_WORDS for word in lowered):
        return False
    if any(_stem(word) in _VERBAL_STEMS for word in lowered):
        return False
    return True


def mine_concepts(chunks: list[Chunk]) -> dict[str, _Candidate]:
    """Mine the corpus vocabulary with C-value, returning survivors by normal form.

    C-value scores a candidate by how often it occurs and how long it is, then
    subtracts the occurrences it only had because it was sitting inside a longer
    candidate. That subtraction is the whole point: "treatment" occurs constantly
    but almost always inside "restorative treatment", "emergency treatment",
    "psychological therapy treatment" — so it scores low and the specific terms
    score high, which is the opposite of what raw frequency would tell us.
    """
    candidates: dict[str, _Candidate] = {}
    # Longer phrase -> the shorter phrases nested inside it, for the discount.
    containment: dict[str, set[str]] = defaultdict(set)

    for chunk in chunks:
        for words in _segments(chunk.text):
            # Longest-first so a shorter phrase always knows the longer one exists.
            for size in range(min(MAX_TERM_WORDS, len(words)), 0, -1):
                for start in range(len(words) - size + 1):
                    window = words[start : start + size]
                    if not _is_valid_phrase(window):
                        continue
                    key = _normalise_phrase(window)
                    if not key:
                        continue
                    candidate = candidates.get(key)
                    if candidate is None:
                        candidate = _Candidate(words=size)
                        candidates[key] = candidate
                    candidate.total_occurrences += 1
                    candidate.chunk_ids.add(chunk.chunk_id)
                    candidate.surfaces[" ".join(window).lower()] += 1

                    # Record every shorter phrase this window contains.
                    for inner_size in range(1, size):
                        for inner_start in range(size - inner_size + 1):
                            inner = window[inner_start : inner_start + inner_size]
                            if not _is_valid_phrase(inner):
                                continue
                            inner_key = _normalise_phrase(inner)
                            if inner_key and inner_key != key:
                                containment[key].add(inner_key)

    for longer_key, inner_keys in containment.items():
        longer = candidates.get(longer_key)
        if longer is None:
            continue
        for inner_key in inner_keys:
            inner = candidates.get(inner_key)
            if inner is None:
                continue
            inner.nested_occurrences += longer.total_occurrences
            inner.nested_in += 1

    scored: list[tuple[float, str, _Candidate]] = []
    for key, candidate in candidates.items():
        if candidate.chunk_frequency < MIN_CONCEPT_CHUNK_FREQ:
            continue
        length_weight = math.log2(candidate.words + 1)
        if candidate.nested_in == 0:
            c_value = length_weight * candidate.total_occurrences
        else:
            c_value = length_weight * (
                candidate.total_occurrences - candidate.nested_occurrences / candidate.nested_in
            )
        if c_value <= 0:
            continue
        # Single words need to earn their place; a one-word candidate with the
        # same C-value as a two-word one is far more likely to be a generic noun.
        if candidate.words == 1:
            c_value *= 0.55
        scored.append((c_value, key, candidate))

    # Sort by score, then key, so ties never depend on dict ordering.
    scored.sort(key=lambda item: (-item[0], item[1]))
    keep = max(MIN_CONCEPTS, min(MAX_CONCEPTS, int(len(chunks) * CONCEPTS_PER_CHUNK)))
    return {key: candidate for _, key, candidate in scored[:keep]}


# --------------------------------------------------------------------------
# Pattern entities
# --------------------------------------------------------------------------


def _normalise_money(surface: str) -> str:
    digits = re.sub(r"[^\d.]", "", surface.replace(",", ""))
    if not digits:
        return surface.strip().lower()
    try:
        amount = float(digits)
    except ValueError:
        return surface.strip().lower()
    rendered = f"{amount:,.0f}" if amount.is_integer() else f"{amount:,.2f}"
    return f"€{rendered}"


def extract_pattern_entities(text: str) -> list[tuple[str, str, str]]:
    """Return ``(node_type, subtype, normalised_surface)`` for every pattern hit."""
    found: list[tuple[str, str, str]] = []

    for match in _MONEY_RE.finditer(text):
        found.append(("value", "amount", _normalise_money(match.group(0))))

    for match in _RATE_RE.finditer(text):
        surface = re.sub(r"\s+", " ", match.group(0).strip().lower())
        surface = surface.replace(" %", "%")
        found.append(("value", "rate", surface))

    for subtype, pattern in _CONSTRAINT_PATTERNS:
        for match in pattern.finditer(text):
            surface = re.sub(r"\s+", " ", match.group(0).strip().lower())
            found.append(("constraint", subtype, surface))

    return found


# --------------------------------------------------------------------------
# Chunk similarity
# --------------------------------------------------------------------------


def _tfidf_vectors(chunks: list[Chunk]) -> dict[str, dict[str, float]]:
    """L2-normalised TF-IDF vector per chunk, over the retriever's own tokens.

    Deliberately the same tokenisation the retriever uses, so ``similar_to``
    describes the space retrieval actually operates in rather than a second,
    differently-tokenised opinion about the corpus.
    """
    total = len(chunks)
    document_freq: Counter[str] = Counter()
    for chunk in chunks:
        document_freq.update(set(chunk.tokens))

    vectors: dict[str, dict[str, float]] = {}
    for chunk in chunks:
        vector: dict[str, float] = {}
        for term, count in chunk.term_counts.items():
            df = document_freq.get(term, 0)
            if df == 0:
                continue
            idf = math.log((total + 1) / (df + 0.5))
            if idf <= 0:
                continue
            vector[term] = (1 + math.log(count)) * idf
        norm = math.sqrt(sum(weight * weight for weight in vector.values()))
        vectors[chunk.chunk_id] = (
            {term: weight / norm for term, weight in vector.items()} if norm else {}
        )
    return vectors


def _nearest_chunks(
    chunks: list[Chunk], vectors: dict[str, dict[str, float]]
) -> list[tuple[str, str, float]]:
    """Top-``MAX_SIMILAR_PER_CHUNK`` neighbours per chunk, de-duplicated.

    An inverted index keeps this off the O(n²) path: only chunks sharing at
    least one term are ever scored, which on a corpus of benefit tables is a
    small fraction of the pairs.
    """
    postings: dict[str, list[str]] = defaultdict(list)
    for chunk_id, vector in vectors.items():
        for term in vector:
            postings[term].append(chunk_id)

    edges: dict[tuple[str, str], float] = {}
    for chunk in chunks:
        vector = vectors[chunk.chunk_id]
        if not vector:
            continue
        scores: dict[str, float] = defaultdict(float)
        for term, weight in vector.items():
            for other_id in postings[term]:
                if other_id == chunk.chunk_id:
                    continue
                scores[other_id] += weight * vectors[other_id].get(term, 0.0)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        for other_id, score in ranked[:MAX_SIMILAR_PER_CHUNK]:
            if score < MIN_SIMILARITY:
                break
            pair = (chunk.chunk_id, other_id) if chunk.chunk_id < other_id else (other_id, chunk.chunk_id)
            edges[pair] = max(edges.get(pair, 0.0), score)

    return [(left, right, round(score, 4)) for (left, right), score in sorted(edges.items())]


# --------------------------------------------------------------------------
# Co-occurrence and communities
# --------------------------------------------------------------------------


def _co_occurrence_edges(
    concept_chunks: dict[str, set[str]], total_chunks: int
) -> list[tuple[str, str, float, int]]:
    """Concept pairs ranked by NPMI, capped per concept.

    NPMI lands in [-1, 1]: 1 when two terms only ever appear together, 0 when
    they co-occur exactly as often as chance would predict. Ranking on it rather
    than on raw counts is what keeps "waiting period" from linking to every
    other common term in the corpus purely because it is common.
    """
    pair_counts: Counter[tuple[str, str]] = Counter()
    concept_ids = sorted(concept_chunks)

    chunk_to_concepts: dict[str, list[str]] = defaultdict(list)
    for concept_id in concept_ids:
        for chunk_id in concept_chunks[concept_id]:
            chunk_to_concepts[chunk_id].append(concept_id)

    for members in chunk_to_concepts.values():
        members.sort()
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                pair_counts[(left, right)] += 1

    scored: list[tuple[str, str, float, int]] = []
    for (left, right), count in pair_counts.items():
        if count < MIN_CO_OCCURRENCE:
            continue
        p_left = len(concept_chunks[left]) / total_chunks
        p_right = len(concept_chunks[right]) / total_chunks
        p_pair = count / total_chunks
        if p_pair <= 0 or p_left <= 0 or p_right <= 0:
            continue
        pmi = math.log(p_pair / (p_left * p_right))
        npmi = pmi / -math.log(p_pair) if p_pair < 1 else 0.0
        if npmi <= 0:
            continue
        scored.append((left, right, round(npmi, 4), count))

    # Cap per concept, from the strongest association down, keeping an edge as
    # soon as either endpoint still has room.
    scored.sort(key=lambda item: (-item[2], item[0], item[1]))
    degree: Counter[str] = Counter()
    kept: list[tuple[str, str, float, int]] = []
    for left, right, npmi, count in scored:
        if degree[left] >= MAX_CO_OCCURS_PER_CONCEPT and degree[right] >= MAX_CO_OCCURS_PER_CONCEPT:
            continue
        kept.append((left, right, npmi, count))
        degree[left] += 1
        degree[right] += 1
    return kept


def detect_communities(
    concept_ids: list[str], edges: list[tuple[str, str, float, int]]
) -> dict[str, int]:
    """Label propagation over the weighted concept graph.

    Each node adopts the label carrying the most weight among its neighbours,
    swept repeatedly until nothing changes. It is near-linear and needs no
    target cluster count — which matters here, because the number of themes in
    a corpus is exactly the thing we do not know in advance.

    Randomised sweep order is what usually makes label propagation
    non-deterministic. Here the order is fixed and ties break on the lowest
    label, so the same corpus always yields the same communities.
    """
    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for left, right, npmi, _count in edges:
        weight = max(npmi, 0.01)
        adjacency[left].append((right, weight))
        adjacency[right].append((left, weight))

    labels: dict[str, str] = {concept_id: concept_id for concept_id in concept_ids}
    ordered = sorted(concept_ids)

    for _ in range(LABEL_PROPAGATION_ROUNDS):
        changed = False
        for concept_id in ordered:
            neighbours = adjacency.get(concept_id)
            if not neighbours:
                continue
            weights: dict[str, float] = defaultdict(float)
            for other_id, weight in neighbours:
                weights[labels[other_id]] += weight
            # Heaviest label wins; ties go to the lexicographically lowest.
            best = min(weights.items(), key=lambda item: (-item[1], item[0]))[0]
            if best != labels[concept_id]:
                labels[concept_id] = best
                changed = True
        if not changed:
            break

    grouped: dict[str, list[str]] = defaultdict(list)
    for concept_id, label in labels.items():
        grouped[label].append(concept_id)

    # Number the surviving communities largest-first so ids are stable and
    # community 0 is always the biggest theme in the corpus.
    survivors = sorted(
        (members for members in grouped.values() if len(members) >= MIN_COMMUNITY_SIZE),
        key=lambda members: (-len(members), sorted(members)[0]),
    )
    assignment: dict[str, int] = {}
    for index, members in enumerate(survivors):
        for concept_id in members:
            assignment[concept_id] = index
    return assignment


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def _section_key(document_id: str, heading: str) -> str:
    return f"section::{document_id}::{_slug(heading)[:60]}"


_TABLE_RULE_RE = re.compile(r"^\|?[\s:|-]*\|[\s:|-]*$")


def _preview(text: str, limit: int = 200) -> str:
    """A one-line, human-readable précis of a passage.

    Most passages in this corpus are Markdown tables, and the raw text of one is
    a wall of pipes, empty leading cells and a `| --- | --- |` rule. Dropped into
    a tooltip or a panel subtitle it reads as line noise. This keeps the cells
    and throws away the scaffolding.

    The full text is untouched — ``excerpt`` still carries the passage exactly as
    indexed, because that is what the retriever scored and what the answer cites.
    This is only what gets shown when there is room for one line.
    """
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _TABLE_RULE_RE.match(stripped):
            continue
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            cells = [cell for cell in cells if cell]
            if not cells:
                continue
            parts.append(" · ".join(cells))
        else:
            parts.append(stripped)

    joined = " — ".join(parts) if parts else text.strip()
    joined = re.sub(r"\s+", " ", joined)
    return joined[:limit] + ("…" if len(joined) > limit else "")


def _specificity(chunk_frequency: int, total_chunks: int) -> float:
    """How informative an entity is, on (0, 1] — inverse document frequency, scaled.

    A term in one chunk out of sixty tells you a great deal about that chunk. A
    term in half the corpus tells you almost nothing. Stored on the node because
    the retriever needs it at traversal time: relevance passing *through* an
    entity is scaled by this, so a walk prefers to travel along "knee
    arthroscopy" rather than along "refund", which touches everything and
    therefore connects nothing in particular.
    """
    if chunk_frequency <= 0 or total_chunks <= 1:
        return 1.0
    frequency = min(chunk_frequency, total_chunks)
    return round(min(1.0, math.log(total_chunks / frequency) / math.log(total_chunks)), 4)


def build_graph(index: KnowledgeIndex | None = None) -> dict[str, Any]:
    """Compile the whole graph from the retriever's index."""
    idx = index if index is not None else build_index()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def add_node(node: dict[str, Any]) -> None:
        if node["id"] in node_ids:
            return
        node_ids.add(node["id"])
        nodes.append(node)

    def add_edge(source: str, target: str, kind: str, **extra: Any) -> None:
        if source not in node_ids or target not in node_ids or source == target:
            return
        edges.append({"id": f"{kind}:{source}->{target}", "kind": kind, "source": source, "target": target, **extra})

    docs_by_id: dict[str, IndexedDoc] = {doc.document_id: doc for doc in idx.docs}
    chunks_by_doc: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in idx.chunks:
        chunks_by_doc[chunk.document_id].append(chunk)

    # ── Documents, governance facets, sections, chunks ──────────────────────
    facet_members: dict[str, set[str]] = defaultdict(set)

    for doc in idx.docs:
        add_node(
            {
                "id": doc.document_id,
                "type": "document",
                "label": doc.document_title,
                "title": doc.document_title,
                "meta": f"{doc.source_type} · {doc.chunk_count} chunks · {doc.char_count:,} characters",
                "department": doc.department,
                "classification": doc.classification,
                "access_group": doc.access_group,
                "version": doc.version,
                "source_filename": doc.source_filename,
                "source_type": doc.source_type,
                "chunk_count": doc.chunk_count,
                "char_count": doc.char_count,
            }
        )

    for kind, attribute in (
        ("department", "department"),
        ("classification", "classification"),
        ("access group", "access_group"),
    ):
        for doc in idx.docs:
            value = getattr(doc, attribute)
            if not value:
                continue
            facet_id = f"facet::{attribute}::{_slug(str(value))}"
            facet_members[facet_id].add(doc.document_id)

    for facet_id, members in sorted(facet_members.items()):
        _, attribute, _ = facet_id.split("::", 2)
        sample = next(iter(sorted(members)))
        value = getattr(docs_by_id[sample], attribute)
        add_node(
            {
                "id": facet_id,
                "type": "facet",
                "label": str(value),
                "title": str(value),
                "meta": f"{attribute.replace('_', ' ')} · {len(members)} document(s)",
                "facet_kind": attribute,
                "document_count": len(members),
            }
        )

    for facet_id, members in sorted(facet_members.items()):
        for document_id in sorted(members):
            add_edge(document_id, facet_id, "governed_by")

    for document_id, chunks in sorted(chunks_by_doc.items()):
        doc = docs_by_id.get(document_id)
        if doc is None:
            continue
        section_chunks: dict[str, list[Chunk]] = defaultdict(list)
        section_order: list[str] = []
        for chunk in chunks:
            heading = chunk.heading or doc.document_title
            if heading not in section_chunks:
                section_order.append(heading)
            section_chunks[heading].append(chunk)

        for position, heading in enumerate(section_order):
            members = section_chunks[heading]
            section_id = _section_key(document_id, heading)
            add_node(
                {
                    "id": section_id,
                    "type": "section",
                    "label": heading,
                    "title": heading,
                    "meta": f"{doc.document_title} · {len(members)} chunk(s)",
                    "document_id": document_id,
                    "document_title": doc.document_title,
                    "position": position,
                    "chunk_count": len(members),
                }
            )
            add_edge(document_id, section_id, "contains", position=position)

        for chunk in chunks:
            heading = chunk.heading or doc.document_title
            excerpt = chunk.text.strip()
            add_node(
                {
                    "id": chunk.chunk_id,
                    "type": "chunk",
                    "label": chunk.chunk_id.rsplit("#", 1)[-1],
                    "title": f"{doc.document_title} · {heading}",
                    "meta": _preview(excerpt),
                    "document_id": document_id,
                    "document_title": doc.document_title,
                    "heading": heading,
                    "excerpt": excerpt,
                    "char_count": len(chunk.text),
                    "token_count": chunk.length,
                }
            )
            add_edge(_section_key(document_id, heading), chunk.chunk_id, "has_chunk")

        # Reading order. This is what lets a table split across two chunks be
        # rejoined at query time — the header row and the row carrying the
        # figure are adjacent, and nothing else in the graph says so.
        for previous, following in zip(chunks, chunks[1:]):
            add_edge(previous.chunk_id, following.chunk_id, "follows")

    # ── Concepts ────────────────────────────────────────────────────────────
    # Ids are assigned here but the nodes are not added until after the mention
    # pass. A concept can clear the corpus-wide C-value bar and still never make
    # any single chunk's salience cut, and adding it regardless leaves a node
    # with no edges — present in every count, connected to nothing, and
    # misleading in the picture.
    mined = mine_concepts(idx.chunks)
    concept_ids: dict[str, str] = {}
    taken: set[str] = set()

    for key in mined:
        concept_id = f"concept::{_slug(key)[:70]}"
        # A slug collision would silently merge two distinct terms.
        if concept_id in taken:
            concept_id = f"{concept_id}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:6]}"
        taken.add(concept_id)
        concept_ids[key] = concept_id

    # ── Mentions, with per-chunk salience so the cap keeps the right ones ───
    total_chunks = max(len(idx.chunks), 1)
    concept_chunks: dict[str, set[str]] = defaultdict(set)
    pattern_nodes: dict[str, dict[str, Any]] = {}
    pattern_chunks: dict[str, set[str]] = defaultdict(set)
    mention_rows: list[tuple[str, str, int, float]] = []

    for chunk in idx.chunks:
        local_counts: Counter[str] = Counter()
        for words in _segments(chunk.text):
            for size in range(min(MAX_TERM_WORDS, len(words)), 0, -1):
                for start in range(len(words) - size + 1):
                    window = words[start : start + size]
                    if not _is_valid_phrase(window):
                        continue
                    key = _normalise_phrase(window)
                    if key in concept_ids:
                        local_counts[key] += 1

        ranked: list[tuple[float, str]] = []
        for key, count in local_counts.items():
            candidate = mined[key]
            idf = math.log((total_chunks + 1) / (candidate.chunk_frequency + 0.5))
            ranked.append(((1 + math.log(count)) * idf, key))
        ranked.sort(key=lambda item: (-item[0], item[1]))

        for salience, key in ranked[:MAX_MENTIONS_PER_CHUNK]:
            concept_id = concept_ids[key]
            concept_chunks[concept_id].add(chunk.chunk_id)
            mention_rows.append((chunk.chunk_id, concept_id, local_counts[key], round(salience, 4)))

        for node_type, subtype, surface in extract_pattern_entities(chunk.text):
            entity_id = f"{node_type}::{subtype}::{_slug(surface)[:60]}"
            if entity_id not in pattern_nodes:
                pattern_nodes[entity_id] = {
                    "id": entity_id,
                    "type": node_type,
                    "label": surface,
                    "title": surface,
                    "meta": subtype.replace("_", " "),
                    "subtype": subtype,
                    "extraction": "pattern",
                }
            pattern_chunks[entity_id].add(chunk.chunk_id)

    # Only now, with the mention pass done, are the surviving concepts known.
    reverse_concept_ids = {concept_id: key for key, concept_id in concept_ids.items()}
    for concept_id in sorted(concept_chunks):
        key = reverse_concept_ids.get(concept_id)
        if key is None:
            continue
        candidate = mined[key]
        chunk_frequency = len(concept_chunks[concept_id])
        add_node(
            {
                "id": concept_id,
                "type": "concept",
                "label": candidate.surface,
                "title": candidate.surface,
                "meta": f"{chunk_frequency} chunk(s) · {candidate.total_occurrences} mention(s)",
                "normal_form": key,
                "words": candidate.words,
                "chunk_frequency": chunk_frequency,
                "occurrences": candidate.total_occurrences,
                "specificity": _specificity(chunk_frequency, total_chunks),
                "extraction": "c-value",
            }
        )

    for entity_id, node in sorted(pattern_nodes.items()):
        chunk_frequency = len(pattern_chunks[entity_id])
        node["chunk_frequency"] = chunk_frequency
        node["specificity"] = _specificity(chunk_frequency, total_chunks)
        node["meta"] = f"{node['subtype'].replace('_', ' ')} · {chunk_frequency} chunk(s)"
        add_node(node)

    for chunk_id, concept_id, count, salience in mention_rows:
        add_edge(chunk_id, concept_id, "mentions", count=count, salience=salience)

    for entity_id, chunk_ids in sorted(pattern_chunks.items()):
        for chunk_id in sorted(chunk_ids):
            add_edge(chunk_id, entity_id, "mentions", count=1, salience=1.0)

    # ── Co-occurrence and communities ───────────────────────────────────────
    co_occurrence = _co_occurrence_edges(dict(concept_chunks), total_chunks)
    for left, right, npmi, count in co_occurrence:
        add_edge(left, right, "co_occurs", npmi=npmi, count=count)

    assignment = detect_communities(sorted(concept_chunks), co_occurrence)
    community_members: dict[int, list[str]] = defaultdict(list)
    for concept_id, community_index in assignment.items():
        community_members[community_index].append(concept_id)

    concept_by_id = {node["id"]: node for node in nodes if node["type"] == "concept"}
    community_summaries: list[dict[str, Any]] = []

    for community_index in sorted(community_members):
        members = sorted(
            community_members[community_index],
            key=lambda concept_id: (
                -concept_by_id[concept_id]["chunk_frequency"],
                concept_by_id[concept_id]["label"],
            ),
        )
        # Name the community after its most widely-attested members. Nothing
        # smarter is warranted: a generated label would be a model output
        # dressed up as a finding.
        headline = [concept_by_id[concept_id]["label"] for concept_id in members[:3]]
        label = ", ".join(headline)
        community_id = f"community::{community_index}"
        covered_chunks: set[str] = set()
        for concept_id in members:
            covered_chunks |= concept_chunks[concept_id]

        add_node(
            {
                "id": community_id,
                "type": "community",
                "label": label,
                "title": label,
                "meta": f"{len(members)} concepts across {len(covered_chunks)} chunk(s)",
                "community_index": community_index,
                "concept_count": len(members),
                "chunk_count": len(covered_chunks),
                "members": [concept_by_id[concept_id]["label"] for concept_id in members],
            }
        )
        community_summaries.append(
            {
                "id": community_id,
                "index": community_index,
                "label": label,
                "concept_count": len(members),
                "chunk_count": len(covered_chunks),
                "members": [concept_by_id[concept_id]["label"] for concept_id in members],
            }
        )
        for concept_id in members:
            add_edge(concept_id, community_id, "in_community")

    # ── Chunk similarity ────────────────────────────────────────────────────
    vectors = _tfidf_vectors(idx.chunks)
    chunk_document = {chunk.chunk_id: chunk.document_id for chunk in idx.chunks}
    for left, right, score in _nearest_chunks(idx.chunks, vectors):
        add_edge(
            left,
            right,
            "similar_to",
            score=score,
            cross_document=chunk_document.get(left) != chunk_document.get(right),
        )

    # ── Stats ───────────────────────────────────────────────────────────────
    type_counts: Counter[str] = Counter(node["type"] for node in nodes)
    kind_counts: Counter[str] = Counter(edge["kind"] for edge in edges)
    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1

    cross_document_similar = sum(
        1 for edge in edges if edge["kind"] == "similar_to" and edge.get("cross_document")
    )
    isolated = sum(1 for node in nodes if degree[node["id"]] == 0)

    stats = {
        "documents": type_counts.get("document", 0),
        "sections": type_counts.get("section", 0),
        "chunks": type_counts.get("chunk", 0),
        "concepts": type_counts.get("concept", 0),
        "values": type_counts.get("value", 0),
        "constraints": type_counts.get("constraint", 0),
        "communities": type_counts.get("community", 0),
        "facets": type_counts.get("facet", 0),
        "nodes": len(nodes),
        "edges": len(edges),
        "isolated_nodes": isolated,
        "cross_document_similar_edges": cross_document_similar,
        "mean_degree": round(sum(degree.values()) / len(nodes), 2) if nodes else 0.0,
        "edges_by_kind": dict(sorted(kind_counts.items())),
        "nodes_by_type": dict(sorted(type_counts.items())),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "backend/scripts/build_knowledge_graph.py",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "corpus_fingerprint": corpus_fingerprint(idx),
        "is_mock_corpus": True,
        "corpus_notice": (
            "Compiled from a synthetic corpus. The scheme names, benefit values and conditions in "
            "the source documents are invented for research use; the graph structure, the "
            "extraction methods and the counts are real output of the pipeline in this repository."
        ),
        "method": {
            "concepts": (
                f"C-value term extraction over 1–{MAX_TERM_WORDS} word phrases, keeping terms seen in "
                f"at least {MIN_CONCEPT_CHUNK_FREQ} chunks. The vocabulary is capped at "
                f"{CONCEPTS_PER_CHUNK} concepts per chunk (floor {MIN_CONCEPTS}, ceiling "
                f"{MAX_CONCEPTS}), taken in C-value order."
            ),
            "values_and_constraints": (
                "Regular-expression extraction of money amounts, percentage rates, frequency limits, "
                "waiting periods, durations and age bounds."
            ),
            "mentions": (
                f"Up to {MAX_MENTIONS_PER_CHUNK} concepts per chunk, ranked by TF-IDF salience within "
                "that chunk. Every pattern entity found in a chunk is kept."
            ),
            "co_occurs": (
                f"Normalised pointwise mutual information over concept pairs seen together at least "
                f"{MIN_CO_OCCURRENCE} times, capped at {MAX_CO_OCCURS_PER_CONCEPT} edges per concept."
            ),
            "communities": (
                f"Deterministic label propagation, {LABEL_PROPAGATION_ROUNDS} sweeps maximum, "
                f"dissolving communities smaller than {MIN_COMMUNITY_SIZE} concepts."
            ),
            "similar_to": (
                f"Cosine similarity over L2-normalised TF-IDF vectors, top {MAX_SIMILAR_PER_CHUNK} "
                f"neighbours per chunk above {MIN_SIMILARITY}."
            ),
        },
        "node_types": NODE_TYPE_DESCRIPTIONS,
        "edge_types": EDGE_TYPE_DESCRIPTIONS,
        "stats": stats,
        "communities": community_summaries,
        "documents": [
            {
                "document_id": doc.document_id,
                "title": doc.document_title,
                "department": doc.department,
                "classification": doc.classification,
                "access_group": doc.access_group,
                "source_type": doc.source_type,
                "chunk_count": doc.chunk_count,
                "char_count": doc.char_count,
            }
            for doc in idx.docs
        ],
        "nodes": nodes,
        "edges": edges,
    }


def corpus_fingerprint(index: KnowledgeIndex) -> str:
    """Hash of the corpus the graph was compiled from.

    Stored in the snapshot and re-checked when it is loaded, so a graph built
    before the last upload announces itself as stale instead of quietly
    describing a corpus that no longer exists.
    """
    digest = hashlib.sha256()
    for name, mtime, size in index.signature:
        digest.update(f"{name}:{mtime:.0f}:{size}\n".encode("utf-8"))
    return digest.hexdigest()[:16]


NODE_TYPE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "document": {
        "label": "Document",
        "description": "One converted source file, carrying the governance metadata captured at upload.",
    },
    "section": {
        "label": "Section",
        "description": "One heading and the body under it — the document's own outline.",
    },
    "chunk": {
        "label": "Chunk",
        "description": "One retrieval unit. This is the passage the retriever scores and the answer cites.",
    },
    "concept": {
        "label": "Concept",
        "description": "A domain term mined from the corpus by C-value, not from any supplied vocabulary.",
    },
    "value": {
        "label": "Value",
        "description": "A money amount or a percentage rate found by pattern.",
    },
    "constraint": {
        "label": "Constraint",
        "description": "A frequency limit, waiting period, duration or age bound found by pattern.",
    },
    "community": {
        "label": "Community",
        "description": "A cluster of concepts that keep occurring together, found by label propagation.",
    },
    "facet": {
        "label": "Governance facet",
        "description": "A department, classification or access group recorded on a document at upload.",
    },
}

EDGE_TYPE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "contains": {
        "label": "contains",
        "inverse": "section of",
        "description": "The document's outline: which sections it is made of.",
    },
    "has_chunk": {
        "label": "has chunk",
        "inverse": "chunk of",
        "description": "Which retrieval units came out of which section.",
    },
    "follows": {
        "label": "follows",
        "inverse": "precedes",
        "description": "Reading order. Rejoins a table or a clause split across two chunks.",
    },
    "mentions": {
        "label": "mentions",
        "inverse": "mentioned in",
        "description": "A concept, amount or constraint occurring in this passage.",
    },
    "co_occurs": {
        "label": "co-occurs with",
        "inverse": "co-occurs with",
        "description": "Two concepts associated more strongly than chance, ranked by NPMI.",
    },
    "in_community": {
        "label": "in community",
        "inverse": "groups",
        "description": "The theme this concept was clustered into.",
    },
    "similar_to": {
        "label": "similar to",
        "inverse": "similar to",
        "description": "Nearest passage by TF-IDF cosine — usually the same benefit in another document.",
    },
    "governed_by": {
        "label": "governed by",
        "inverse": "governs",
        "description": "The department, classification or access group recorded on this document.",
    },
}
