# Architecture

## System Overview

The Knowledge Hub Markdown Converter is a conversion-first MVP for building a governed knowledge ingestion pipeline. It converts business documents (Excel, PDF, Word, CSV, PowerPoint, etc.) into clean, reviewable Markdown with attached metadata, confidence scoring, and optional AI-enhanced extraction.

Markdown is the human-reviewable and AI-friendly intermediate format. On top of the converter, the app also ships a working local RAG assistant: `services/rag_service.py` chunks the converted Markdown, retrieves passages with a pure-Python BM25 pass, and has Azure OpenAI answer strictly from what was retrieved. Moving that retrieval layer to a managed vector index is the remaining production step.

Above retrieval sits a **knowledge graph** compiled from the same corpus. `services/graph_builder.py` mines it — concepts, values, constraints and themes, all derived from the converted Markdown with no hand-authored ontology and no model call — and writes a single snapshot to `data/knowledge_graph.json`. That one snapshot serves two consumers: the graph explorer in the UI draws it, and `services/graph_rag.py` walks it to expand lexical hits before re-ranking. The picture and the retrieval are never two different structures.

## Data Flow

```
User uploads document via React UI
    ↓
FastAPI Backend (backend/app/)
    ├── Validate: File type, size, filename
    ├── Save: Raw source file → data/local/raw_uploads/
    ├── Route: Excel → openpyxl custom converter
    │          Other → MarkItDown (pip dependency)
    ├── AI Plugin (optional): Azure OpenAI image description / OCR
    ├── Score: Confidence scorer evaluates output quality
    ├── Attach: Metadata frontmatter (document_id, department, etc.)
    ├── Save: Markdown output → data/local/markdown_outputs/
    ├── Save: JSON manifest → data/local/manifests/
    └── Return: Markdown + metadata + confidence + warnings
    ↓
React Frontend (frontend/)
    ├── Output tab: Markdown preview, confidence, warnings, copy/download
    ├── Clean Data tab (new):
    │     ├── POST /api/clean → AI-readability transforms
    │     ├── Toggleable steps: skip hidden sheets, pivot→long-form,
    │     │     collapse repeated runs, promote glossary, split multi-block,
    │     │     PDF table recovery, relative paths, whitespace, AI summary
    │     └── POST /api/clean/save → promote cleaned MD to canonical;
    │           backs original up to {document_id}.raw.md and records
    │           cleaning_log + ai_summary in the manifest
    └── Capabilities panel: What the system can/cannot do
```

## Component Relationships

| Component | File | Depends On | Depended On By |
|-----------|------|-----------|----------------|
| FastAPI app | `backend/app/main.py` | config, models, services | Frontend (HTTP) |
| Config | `backend/app/config.py` | `.env` | main, all services |
| Models | `backend/app/models.py` | pydantic | main, services |
| Markdown converter | `services/markdown_converter.py` | openpyxl, MarkItDown, ai_plugin, confidence_scorer | main.py |
| Confidence scorer | `services/confidence_scorer.py` | openpyxl, models | markdown_converter |
| AI plugin | `services/ai_plugin.py` | Azure OpenAI, MarkItDown | markdown_converter |
| File analyzer | `services/file_analyzer.py` | openpyxl, pdfplumber | main.py |
| Data cleaner | `services/data_cleaner.py` | openpyxl (indirect via manifest), pdfplumber, ai_plugin | main.py (`/api/clean`, `/api/clean/save`) |
| RAG service | `services/rag_service.py` | config, models, ai_plugin | main.py (`/api/chat`, `/api/knowledge/*`), graph_builder, graph_rag |
| Graph builder | `services/graph_builder.py` | rag_service (chunks, index, tokenizer) | `scripts/build_knowledge_graph.py` |
| Graph store | `services/graph_store.py` | config (`KNOWLEDGE_GRAPH_PATH`) | main.py (`/api/graph/knowledge`), graph_rag |
| Graph RAG | `services/graph_rag.py` | graph_store, rag_service | rag_service (`mode="graph"`) |
| MarkItDown | pip package (`requirements.txt`) | (library) | markdown_converter, ai_plugin |
| React shell | `frontend/src/App.tsx` | api.ts, all pages | End users |
| Chat page | `frontend/src/pages/ChatPage.tsx` | api.ts | App.tsx |
| Documents page | `frontend/src/pages/DocumentsPage.tsx` | api.ts | App.tsx |
| Knowledge graph view | `frontend/src/pages/KnowledgeGraphView.tsx` | api.ts, knowledgeGraphModel, GraphCanvas | DocumentsPage |
| Graph canvas | `frontend/src/pages/GraphCanvas.tsx` | 3d-force-graph, three | KnowledgeGraphView |

## Key Design Decisions

1. **Custom Excel converter** — openpyxl instead of MarkItDown for Excel, producing cleaner sheet-level Markdown tables with inferred headers, merged cell handling, and sheet context blocks.
2. **Confidence scoring** — Every conversion returns a scored breakdown (structure, content, images, formatting) so users know how much to trust the output.
3. **AI plugin is optional** — Azure OpenAI image description and OCR are a toggle, not a requirement. Confidential/Restricted documents block the plugin automatically.
4. **File analyzer endpoint** — `/api/analyze` pre-scans a file before conversion to detect images and recommend whether the AI plugin is needed.
5. **Metadata frontmatter** — Every Markdown output begins with YAML metadata (document_id, department, access_group, classification, etc.) for future RAG retrieval filters.
6. **Local storage only** — Phase 1 saves raw files, Markdown outputs, and JSON manifests to `data/local/`. No cloud storage yet.
7. **SHA-256 deduplication** — File hashes are stored in manifests to support future incremental ingestion.
8. **Clean Data tab** — A separate tab in the UI applies AI-readability transforms (skip hidden sheets, reshape pivot tables to long form, collapse repeated value runs, promote glossaries, recover PDF tables via `pdfplumber`, optional Azure OpenAI 3-sentence summary). The cleaner is non-destructive: the original Markdown stays on the Output tab unless the user clicks **Save as canonical**, which backs the original up to `{document_id}.raw.md` and records a `cleaning_log` in the manifest.
9. **AI summary security gate** — `/api/clean` blocks the AI summary at the API boundary for `Confidential` / `Restricted` classifications, mirroring the existing rule for the conversion AI plugin.

## Directory Structure

```
markdown_converter/
├── backend/
│   ├── run.py                         # Uvicorn entry point
│   ├── batch_convert.py               # Convert a whole folder from the CLI
│   ├── .env.example                   # Template — copy to .env (never committed)
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── app/
│   │   ├── main.py                    # FastAPI app and all API routes
│   │   ├── config.py                  # Paths, upload limits, Azure config
│   │   ├── models.py                  # Pydantic request/response models
│   │   └── services/
│   │       ├── markdown_converter.py  # Core conversion logic (Excel + MarkItDown)
│   │       ├── confidence_scorer.py   # Quality scoring engine
│   │       ├── ai_plugin.py           # Azure OpenAI client, image/OCR plugin, pricing
│   │       ├── file_analyzer.py       # Pre-conversion file analysis
│   │       ├── data_cleaner.py        # AI-readability cleaner for the Clean Data tab
│   │       ├── rag_service.py         # Chunking, BM25 retrieval, grounded answers
│   │       ├── graph_builder.py       # Compiles the knowledge graph from the corpus
│   │       ├── graph_store.py         # Loads the snapshot and answers neighbour queries
│   │       └── graph_rag.py           # Graph-expansion retrieval and re-ranking
│   ├── scripts/
│   │   ├── build_mock_corpus.py       # Builds the synthetic corpus via the real converter
│   │   ├── mock_corpus_content.py     # The nine documents' text, read by the builder
│   │   └── build_knowledge_graph.py   # Compiles data/knowledge_graph.json
│   └── tests/
├── frontend/
│   └── src/
│       ├── App.tsx                    # Shell: top bar, tab switching
│       ├── api.ts                     # API base URL, response types, fetch helpers
│       ├── pages/ChatPage.tsx         # Knowledge Assistant (RAG chat, core/graph modes)
│       ├── pages/DocumentsPage.tsx    # Upload, preview, confidence, Clean Data tab
│       ├── pages/KnowledgeGraphView.tsx # Graph explorer, filters, retrieval trace replay
│       ├── pages/GraphCanvas.tsx      # 3d-force-graph renderer
│       ├── pages/knowledgeGraphModel.ts # Graph types, projection and filtering
│       └── styles.css
├── data/
│   ├── knowledge_graph.json           # Compiled graph snapshot (built, not hand-written)
│   └── local/
│       ├── raw_uploads/               # Saved source files
│       ├── markdown_outputs/          # Converted Markdown files
│       ├── manifests/                 # JSON metadata manifests
│       └── chat_feedback.jsonl        # Thumbs up/down log
├── start.bat                          # One-click setup and launch (Windows)
└── docs/                              # All documentation (this folder)
```

## RAG Pipeline (implemented)

`services/rag_service.py`, exposed at `POST /api/chat`:

```
Markdown outputs (data/local/markdown_outputs/)
    ↓
In-memory index (rebuilt when the corpus fingerprint changes)
    ├── Split on ATX headings, then into overlapping ~1400-char windows
    ├── Tokenize: lowercase → drop stopwords → conservative stemmer
    └── chunk_id = {document_id}#{n}
    ↓
Retrieval
    ├── Query terms absent from the index expand by prefix (physio → physiotherapy)
    ├── Rank by BM25
    └── Gate on query-term COVERAGE, not BM25 score — IDF collapses on a small
        corpus, so the score is unusable as a relevance threshold
    ↓
Grounded generation
    ├── Below the coverage gate → never call the model, return the fallback
    ├── Azure OpenAI answers as JSON, restricted to the retrieved passages
    └── Reported confidence is capped by what retrieval actually supports
```

## Knowledge Graph Pipeline (implemented)

`services/graph_builder.py`, compiled by `scripts/build_knowledge_graph.py`, served at `GET /api/graph/knowledge`:

```
Markdown outputs (data/local/markdown_outputs/)
    ↓
Reuses the RAG index — same chunks, same tokenizer, so a node in the graph
is the same unit the retriever scores
    ↓
Extraction (deterministic, no model call anywhere)
    ├── Concepts   — C-value over 1–4 word phrases (Frantzi et al., 2000)
    ├── Values     — money amounts and percentages, by pattern
    ├── Constraints— frequency limits, waiting periods, age bounds, by pattern
    ├── co_occurs  — concept pairs ranked by NPMI, not raw frequency
    ├── Communities— label propagation (Raghavan et al., 2007), made deterministic
    └── similar_to — TF-IDF cosine between chunks, mostly cross-document
    ↓
Snapshot → data/knowledge_graph.json
    ├── 8 node types: document, section, chunk, concept, value, constraint,
    │                 community, facet
    ├── 8 relations: contains, has_chunk, follows, mentions, co_occurs,
    │                in_community, similar_to, governed_by
    ├── `method` block records how it was made
    └── `corpus_fingerprint` ties it to the corpus it came from
```

The snapshot is compiled to a file rather than rebuilt per request, because it is a citable artefact — the counts quoted in the write-up and the retrieval traces behind the evaluation all refer to one file with one fingerprint. After an upload the API compares fingerprints and marks the snapshot `stale` rather than drawing a picture of a corpus that has moved on.

### Graph-expansion retrieval

`services/graph_rag.py`, reached with `POST /api/chat` and `mode: "graph"`:

```
BM25 hits (top 8) become seeds
    ↓
Question terms also link to concept nodes, so a question can enter the graph
even where no single passage scores well
    ↓
Relevance propagates 2 hops along typed edges
    ├── Per-relation weight: mentions 1.00, similar_to 0.80, follows 0.55,
    │   co_occurs 0.45, has_chunk 0.30, in_community 0.20
    ├── contains and governed_by are weighted 0 — visible in the picture,
    │   never walked, or every passage in a document would reach every other
    └── Per-hop decay 0.55; branches below a mass floor are dropped
    ↓
Fuse normalised lexical (0.6) with normalised graph (0.4), re-rank
    ↓
Every source reports origin (seed / expanded), hop count and the term that
brought it in — the same trace the graph view replays
```

**Expansion never opens the gate.** If no seed passage clears the coverage threshold, the answer is still the "I could not find this" fallback, and if no snapshot exists the mode falls back to `core` and says so in `retrieval.fell_back`. The graph widens the evidence behind an answer; it never manufactures one.

### Remaining production work

| Step | Why it is still open |
|------|----------------------|
| Azure AI Search / vector DB | Replaces the in-memory BM25 index; adds hybrid dense+sparse search |
| Embeddings | `AZURE_EMBEDDING_DEPLOYMENT` is configured but not yet used |
| Entra ID authentication | Needed before `access_group` and `classification` can be enforced rather than merely recorded |
| Metadata filters at query time | Filter by department / access group / expiry before the LLM call |
