# API Reference

## Base URL
```
http://127.0.0.1:8000
```

## Interactive Docs
```
http://127.0.0.1:8000/docs   (Swagger UI)
```

No authentication is required in Phase 1 (local MVP).

---

## Health Check

### GET /api/health
Returns backend status.

**Response (200):**
```json
{ "status": "ok" }
```

---

## File Analysis

### POST /api/analyze
Pre-scan a file before conversion to detect images and recommend conversion mode. Used by the frontend to decide whether to suggest enabling the AI plugin.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | Yes | File to analyze |
| `classification` | string | No | `General`, `Internal`, `Confidential`, `Restricted` (default: `Internal`) |

**Response (200):**
```json
{
  "filename": "Cardiac list - Jan 26.xlsx",
  "extension": ".xlsx",
  "file_size_bytes": 45230,
  "has_images": true,
  "image_count": 3,
  "has_merged_cells": true,
  "sheet_count": 6,
  "recommendation": "ai_recommended",
  "recommendation_reason": "Excel file contains 3 embedded image(s). AI plugin will describe them.",
  "ai_blocked": false,
  "ai_blocked_reason": null
}
```

**Recommendation values:**
| Value | Meaning |
|-------|---------|
| `standard` | Standard conversion will capture everything |
| `ai_recommended` | Images detected; AI plugin will improve output |
| `ai_required` | Scanned/image-only PDF; AI plugin is needed for any text |

---

## Convert Document

### POST /api/convert
Upload and convert a document to Markdown. Saves raw file, Markdown output, and JSON manifest locally.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | Yes | Document to convert (max 75 MB) |
| `document_title` | string | No | Human-readable title (defaults to filename stem) |
| `department` | string | No | e.g. `Claims`, `HR`, `Finance` |
| `access_group` | string | No | e.g. `KH_CLAIMS_USERS` |
| `classification` | string | No | `General`, `Internal`, `Confidential`, `Restricted` (default: `Internal`) |
| `document_owner` | string | No | Business owner responsible for content |
| `additional_context` | string | No | Category, missing-field explanation, or business rules |
| `version` | string | No | e.g. `v1.0` |
| `expiry_review_date` | string | No | ISO date e.g. `2026-12-01` |
| `enable_plugin` | string | No | `true` or `false` (default: `false`). Blocked for Confidential/Restricted. |

**Response (200):**
```json
{
  "markdown": "---\ndocument_id: \"cardiac-list-jan-26-4b6458edcc3e\"\n...",
  "metadata": {
    "document_id": "cardiac-list-jan-26-4b6458edcc3e",
    "document_title": "Cardiac list - Jan 26",
    "source_filename": "Cardiac list - Jan 26.xlsx",
    "source_type": "XLSX",
    "source_path": "data/local/raw_uploads/cardiac-list-jan-26-4b6458edcc3e.xlsx",
    "markdown_path": "data/local/markdown_outputs/cardiac-list-jan-26-4b6458edcc3e.md",
    "file_sha256": "abc123...",
    "file_size_bytes": 45230,
    "converted_at_utc": "2026-06-10T09:00:00+00:00",
    "converter": "openpyxl",
    "extraction_strategy": "openpyxl workbook extraction with sheet-level Markdown tables",
    "department": "Claims",
    "access_group": "KH_CLAIMS_USERS",
    "classification": "Internal",
    "document_owner": null,
    "additional_context": "Category: Cardiac benefit list.",
    "version": null,
    "expiry_review_date": null,
    "sheet_count": 6,
    "sheets": [{"name": "Mater Private", "row_count": 120, ...}]
  },
  "output_file": "data/local/markdown_outputs/cardiac-list-jan-26-4b6458edcc3e.md",
  "manifest_file": "data/local/manifests/cardiac-list-jan-26-4b6458edcc3e.json",
  "warnings": [],
  "confidence": {
    "overall": 78.5,
    "structure_fidelity": 85.0,
    "content_completeness": 90.0,
    "image_handling": 0.0,
    "formatting_preservation": 70.0,
    "factors": ["Merged cells detected: structure may be approximated"],
    "limitations_hit": ["Excel contains embedded images that are NOT extracted"]
  },
  "plugin_used": false,
  "plugin_name": null
}
```

**Error responses:**

| Code | Meaning |
|------|---------|
| 400 | Missing filename, empty file, or unsupported type |
| 413 | File exceeds 75 MB limit |
| 422 | Conversion failed (malformed file, password-protected, etc.) |
| 500 | Internal server error |

**PowerShell example:**
```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/convert" `
  -F "file=@C:\path\to\document.xlsx" `
  -F "department=Claims" `
  -F "access_group=KH_CLAIMS_USERS" `
  -F "additional_context=Category: Cardiac benefit list." `
  -F "classification=Internal" `
  -F "enable_plugin=true"
```

---

## Download Markdown Output

### GET /api/outputs/{filename}
Download a previously saved Markdown output file.

**Path parameter:** `filename` — the `.md` filename from the `output_file` field in the convert response.

**Response (200):** Markdown file download.

**Response (404):** File not found.

---

## Clean Data

### POST /api/clean
Apply AI-readability transforms to a previously converted document. Reads the canonical Markdown at `data/local/markdown_outputs/{document_id}.md` and returns a cleaned version. **Does not mutate disk** — call `/api/clean/save` to persist.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `document_id` | string | Yes | The `document_id` from a previous `/api/convert` response |
| `classification` | string | No | `General`, `Internal`, `Confidential`, `Restricted` (default: `Internal`). `Confidential`/`Restricted` silently disable the AI summary step at the API boundary. |
| `options` | string (JSON) | No | JSON object matching `CleanOptions` (see below). Defaults to all steps enabled. |

**`CleanOptions` JSON shape (all optional; default `true`):**
```json
{
  "skip_hidden_sheets": true,
  "pivot_to_long_form": true,
  "collapse_repeated_runs": true,
  "promote_glossary": true,
  "split_multi_block_sheets": true,
  "normalize_whitespace": true,
  "relative_paths": true,
  "pdf_extract_tables": true,
  "ai_summary": true
}
```

**Response (200):**
```json
{
  "document_id": "cardiac-list-jan-26-4b6458edcc3e",
  "cleaned_markdown": "---\ndocument_id: ...\n---\n\n# Cardiac list - Jan 26\n...",
  "cleaning_log": [
    {
      "id": "skip_hidden_sheets",
      "label": "Skipped hidden / admin sheets",
      "applied": true,
      "note": "Removed: Data",
      "metrics": {"skipped_count": 1, "skipped": ["Data"]}
    }
  ],
  "summary": "This is the January 2026 cardiac procedure list ...",
  "summary_blocked_reason": null,
  "raw_size": 336831,
  "cleaned_size": 934287
}
```

**Error responses:**

| Code | Meaning |
|------|---------|
| 400 | Invalid `options` JSON |
| 404 | `document_id` has no Markdown output on disk |
| 422 | Cleaning failed (corrupt frontmatter, etc.) |

---

### POST /api/clean/save
Promote a cleaned Markdown to canonical. Backs the current canonical Markdown up to `{document_id}.raw.md` (only if no backup exists yet — never overwrites the original raw conversion), writes the cleaned Markdown to `{document_id}.md`, and adds `cleaning_log` + `ai_summary` + `raw_markdown_path` fields to the manifest.

**Request:** `application/json`

```json
{
  "document_id": "cardiac-list-jan-26-4b6458edcc3e",
  "cleaned_markdown": "---\n...\n---\n\n# ...",
  "cleaning_log": [/* CleaningStep entries from /api/clean */],
  "summary": "This is the January 2026 cardiac procedure list ..."
}
```

**Response (200):**
```json
{
  "document_id": "cardiac-list-jan-26-4b6458edcc3e",
  "canonical_path": "data/local/markdown_outputs/cardiac-list-jan-26-4b6458edcc3e.md",
  "raw_backup_path": "data/local/markdown_outputs/cardiac-list-jan-26-4b6458edcc3e.raw.md",
  "manifest_path": "data/local/manifests/cardiac-list-jan-26-4b6458edcc3e.json"
}
```

---

## Capabilities

### GET /api/capabilities
Returns what the system can and cannot do, plus plugin availability status.

**Response (200):**
```json
{
  "supported": [
    {
      "feature": "Excel text & tables (.xlsx/.xlsm)",
      "supported": true,
      "details": "Custom openpyxl converter with merged cells, inferred headers, sheet context",
      "plugin_required": false
    },
    ...
  ],
  "limitations": [
    {
      "feature": "Excel embedded images",
      "supported": false,
      "details": "Images/shapes in Excel cells are silently dropped without AI plugin",
      "plugin_required": true
    },
    ...
  ],
  "file_types": [".xlsx", ".xlsm", ".pdf", ".docx", ".csv", ".pptx", ...],
  "max_file_size_mb": 75,
  "plugin_available": true
}
```

---

## Pricing

### GET /api/pricing
Returns the active chat deployment and its token pricing so the UI can show
cost estimates that match the configured model.

**Response (200):**
```json
{
  "deployment": "gpt-4.1",
  "input_per_1m_tokens_usd": 2.0,
  "output_per_1m_tokens_usd": 8.0,
  "source": "Azure OpenAI pay-as-you-go Global Standard (USD)"
}
```

---

## Knowledge Assistant (RAG chat)

Answers are generated **only** from the converted Markdown in
`data/local/markdown_outputs/`. When retrieval finds nothing relevant the model
is never called, so the assistant cannot invent a policy answer.

### POST /api/chat
Answer an agent's question from the knowledge base.

**Request (JSON):**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `question` | string | Yes | Must not be empty (400 otherwise) |
| `history` | array | No | Prior turns as `{ "role": "user"\|"assistant", "content": "..." }` |
| `top_k` | int | No | Passages to retrieve, 1–12, default 6 |
| `mode` | string | No | `"core"` for BM25 alone, `"graph"` (default) to expand those hits over the knowledge graph before re-ranking |

**Response (200):**
```json
{
  "answered": true,
  "direct_answer": "Dental examinations are refunded at 100%, twice per policy year.",
  "key_details": ["100% refund", "2 per policy year"],
  "important_notes": ["A 26-week waiting period applies"],
  "confidence": "High",
  "sources": [
    {
      "index": 1,
      "chunk_id": "corevita-dental-complete-adcb9944096b#0",
      "document_id": "corevita-dental-complete-adcb9944096b",
      "document_title": "CoreVita Dental Complete",
      "heading": "Investigative & Preventative",
      "excerpt": "Examinations: 100% refund - 2 per year.",
      "score": 4.21,
      "cited": true,
      "origin": "seed",
      "retrieval_reason": "seeded by BM25 at rank 1",
      "lexical_score": 1.0,
      "graph_score": 0.42,
      "hops": 0
    }
  ],
  "follow_ups": ["Is scaling and polishing covered?"],
  "suggested_topics": [],
  "fallback_reason": null,
  "latency_ms": 812,
  "retrieval_ms": 47,
  "model": "gpt-4.1",
  "ai_cost": { "total_cost_usd": 0.00214, "...": "..." },
  "retrieval": {
    "mode": "graph",
    "requested_mode": "graph",
    "fell_back": false,
    "fallback_reason": null,
    "seed_count": 6,
    "expanded_count": 2,
    "linked_concepts": ["waiting period", "restorative treatment"],
    "nodes_reached": 41,
    "edges_traversed": 88,
    "highlight_nodes": ["corevita-dental-complete-adcb9944096b#0"],
    "highlight_edges": [],
    "settings": { "hops": 2, "hop_decay": 0.55, "lexical_weight": 0.6 },
    "graph_stale": false
  }
}
```

The five graph fields on each source (`origin`, `retrieval_reason`,
`lexical_score`, `graph_score`, `hops`) are `null` in `core` mode. In `graph`
mode they explain why each passage is in the answer — `"seed"` when BM25 found
it, `"expanded"` when the walk did, with the hop count and the shared term that
brought it in.

`retrieval` reports what retrieval *actually did*, which is not always what was
asked for: `mode` is the mode that ran and `requested_mode` the one requested.
When `graph` is asked for and no snapshot has been compiled, `fell_back` is
`true`, `fallback_reason` says so, and the question is still answered in `core`
mode rather than failing. `graph_stale` is `true` when the snapshot was compiled
from a different corpus than the one currently indexed.

`answered` is `false` whenever the answer is not grounded. `fallback_reason`
then says why:

| `fallback_reason` | Meaning |
|-------------------|---------|
| `no_documents_indexed` | The knowledge base is empty — convert a document first |
| `no_relevant_passages` | Nothing cleared the query-term coverage gate; the model was not called |
| `model_not_configured` | No Azure OpenAI credentials; the retrieved passages are still returned in `sources` |
| `model_error: …` | The AI call failed; the retrieved passages are still returned in `sources` |
| `model_reported_no_grounded_answer` | The model saw the context and declined to answer from it |

`confidence` is never higher than the retrieval evidence supports, even if the
model claims otherwise.

### GET /api/knowledge/overview
Everything the chat page needs on first load: corpus stats, the indexed
documents, and starter prompts derived from the corpus.

**Response (200):**
```json
{
  "stats": { "document_count": 9, "chunk_count": 38, "model": "gpt-4.1", "model_configured": true },
  "documents": [
    {
      "document_id": "corevita-dental-complete-adcb9944096b",
      "document_title": "CoreVita Dental Complete",
      "source_filename": "CoreVita Dental Complete.docx",
      "source_type": "Word",
      "classification": "Internal",
      "department": "Claims",
      "access_group": "KH_CLAIMS_USERS",
      "version": "May 2026",
      "converted_at_utc": "2026-06-09T13:42:00+00:00",
      "char_count": 3918,
      "chunk_count": 4
    }
  ],
  "suggestions": ["What does CoreVita Dental Complete cover?"]
}
```

### GET /api/knowledge/documents/{document_id}
Returns the stored Markdown for one document so a source can be read in full.

**Response (200):**
```json
{ "document_id": "corevita-dental-complete-adcb9944096b", "markdown": "---\ndocument_id: ...\n---\n\n# ..." }
```

**Errors:** `404` if no document matches (path traversal in `document_id` is
rejected the same way).

### POST /api/chat/feedback
Records a thumbs up/down on an answer, appended as one JSON line to
`data/local/chat_feedback.jsonl`.

**Request (JSON):**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `question` | string | Yes | |
| `answer` | string | Yes | |
| `rating` | string | Yes | `"up"` or `"down"` — anything else is a 400 |
| `comment` | string | No | Free-text detail |
| `confidence` | string | No | Confidence shown with the answer |
| `document_ids` | array | No | Documents behind the answer |

**Response (200):**
```json
{ "recorded": true, "log_path": "data/local/chat_feedback.jsonl" }
```

---

## Knowledge Graph

### GET /api/graph/knowledge

Returns the compiled knowledge graph — the same snapshot the graph-expansion
retriever walks, so the picture the UI draws and the structure retrieval uses
are never two different things.

**Response (200):**
```json
{
  "schema_version": "2.0",
  "generated_at_utc": "2026-08-15T21:29:52+00:00",
  "corpus_fingerprint": "3f9c1a…",
  "nodes": [
    { "id": "corevita-dental-complete-adcb9944096b", "type": "document", "label": "CoreVita Dental Complete" },
    { "id": "concept::waiting period", "type": "concept", "label": "waiting period", "c_value": 18.4 }
  ],
  "edges": [
    { "source": "corevita-dental-complete-adcb9944096b#0", "target": "concept::waiting period", "kind": "mentions", "salience": 2.7 }
  ],
  "stats": { "node_count": 812, "edge_count": 3104, "…": "…" },
  "method": { "…": "how every node and edge type was derived" },
  "stale": false,
  "indexed_corpus_fingerprint": "3f9c1a…"
}
```

| Field | Meaning |
|-------|---------|
| `nodes` / `edges` | 8 node types and 8 relations — see [services.md](services.md#graph_builderpy) for the full schema |
| `stats` | Counts per node type and per relation |
| `method` | How the graph was made, recorded inside the snapshot so it can always explain itself |
| `corpus_fingerprint` | The corpus the snapshot was compiled from |
| `indexed_corpus_fingerprint` | The corpus currently indexed |
| `stale` | `true` when those two fingerprints differ — which happens after an upload |

A `stale` graph is still returned and still drawn; the UI labels it rather than
hiding it. Rebuild with `python backend/scripts/build_knowledge_graph.py`.

**Errors:** `404` when no snapshot has been compiled yet, with a message naming
the script that builds one.
