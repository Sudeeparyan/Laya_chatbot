# Services

All services live in `backend/app/services/`. Each service is a focused module with a single responsibility.

---

## markdown_converter.py

**Purpose:** Core conversion orchestrator. Accepts an uploaded file path and metadata fields, routes to the correct converter, attaches metadata, saves outputs, and returns a `ConversionResponse`.

**Entry point:** `convert_upload(source_file, *, original_filename, department, ...)`

**Depends on:** `openpyxl`, `MarkItDown` (pip package), `ai_plugin`, `confidence_scorer`, `config`, `models`

**Used by:** `app/main.py` — `/api/convert` endpoint

### Routing Logic

```
file extension
├── .xlsx / .xlsm / .xltx / .xltm  → _convert_excel_workbook()
└── all other supported types        → _convert_with_markitdown()
                                        └── optionally calls ai_plugin.convert_with_plugin()
```

### Excel Conversion (`_convert_excel_workbook`)

Extracts each sheet as a Markdown table block with:
- Sheet name header
- Source row/column ranges
- Inferred header row (first non-empty row with enough filled cells)
- Merged cell expansion from anchor value
- Removal of NaN, None, and "Unnamed" artifacts
- Warnings for hidden sheets, very wide sheets (>50 cols), very large sheets (>2000 rows), and multi-block sheets

Output per sheet:
```markdown
## Sheet: Mater Private

- Source rows: 1-120
- Source columns: 1-45
- Header row: 3

### Table
| Code | Plan | Cover |
| --- | --- | --- |
```

### MarkItDown Conversion (`_convert_with_markitdown`)

Routes all non-Excel files through the MarkItDown library installed from `requirements.txt`. If `enable_plugin=True` and the file has images, calls `ai_plugin.convert_with_plugin()` instead.

### Document ID Generation

`document_id = slugify(filename_stem) + "-" + sha256[:12]`

This is deterministic and stable for the same file. Used for all saved filenames (raw, markdown, manifest).

### Output Files Saved

| File | Location | Content |
|------|----------|---------|
| Raw source | `data/local/raw_uploads/{document_id}{ext}` | Original uploaded file |
| Markdown | `data/local/markdown_outputs/{document_id}.md` | Converted Markdown with frontmatter |
| Manifest | `data/local/manifests/{document_id}.json` | Full metadata as JSON |

---

## confidence_scorer.py

**Purpose:** Evaluate the quality of a conversion and return a scored `ConfidenceScore` breakdown.

**Entry point:** `compute_confidence(source_path, markdown_output, *, extension, sheet_metadata, plugin_enabled, images_found, images_described)`

**Depends on:** `openpyxl`, `models`

**Used by:** `services/markdown_converter.py`

### Score Dimensions

| Dimension | Weight | What It Measures |
|-----------|--------|-----------------|
| `structure_fidelity` | 35% | Table/column alignment, merged cell handling |
| `content_completeness` | 35% | All data present, no blank formula cells, all sheets extracted |
| `image_handling` | 15% | Images captured or silently dropped |
| `formatting_preservation` | 15% | Dates, currency, layout representation |

**`overall` = weighted average of the four dimensions.**

### Scoring by File Type

| Extension | Scorer function | Notes |
|-----------|----------------|-------|
| `.xlsx`, `.xlsm`, etc. | `_score_excel()` | Checks merged cells, hidden sheets, image presence, formula blanks |
| `.pdf` | `_score_pdf()` | Checks output length, scanned detection, image content |
| `.docx`, `.doc`, `.pptx` | `_score_office_doc()` | Checks image counts, track changes, slide layouts |
| `.csv`, `.json`, `.xml`, `.txt`, `.md`, `.html` | inline | High confidence; text-based formats |
| Other | inline | Lower baseline; auto-detection |

---

## ai_plugin.py

**Purpose:** Azure OpenAI integration for image description (OCR + visual description), enhanced PDF extraction, and AI cost tracking/estimation.

**Entry points:**
- `is_plugin_available()` → bool — checks if Azure credentials are set
- `analyze_document_images(file_path, extension)` → `tuple[list[ImageAnalysis], AICostEstimate | None]` — extract and analyze images; returns cost estimate alongside analyses
- `convert_with_plugin(source_path)` → `tuple[str, AICostEstimate | None]` — full MarkItDown conversion with image descriptions; returns cost alongside markdown
- `format_image_block(analysis)` → str — format an ImageAnalysis into a Markdown block
- `estimate_cost_for_images(image_count, deployment)` → float — rough pre-conversion USD cost estimate based on image count
- `_get_pricing(deployment_name)` → `(input_per_1m, output_per_1m)` — look up token pricing by deployment name

**Depends on:** `openai` (AzureOpenAI), `MarkItDown`, `config`, `models`

**Used by:** `services/markdown_converter.py`

### ImageAnalysis Dataclass

```python
@dataclass
class ImageAnalysis:
    location: str       # e.g. "Sheet1/B5" or "Page 2"
    ocr_text: str       # Raw text extracted from image
    description: str    # AI description of what image shows
    image_type: str     # "chart" | "table" | "diagram" | "photo" | "logo" | "unknown"
    confidence: float   # 0.0–1.0
    errors: list[str]
```

### Cost Tracking — `_TokenTracker`

All Azure OpenAI API calls are routed through `_TokenTracker`, which wraps the `AzureOpenAI` client. It intercepts `client.chat.completions.create()` and records `(prompt_tokens, completion_tokens)` from each `response.usage`. After all calls complete, `tracker.build_cost_estimate(locations)` produces an `AICostEstimate`.

### Pricing Table (`_MODEL_PRICING`)

Prices are per 1 million tokens (USD, Global Standard deployment, as of 2025-06):

| Model pattern | Input | Output |
|--------------|-------|--------|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-4.1 | $2.00 | $8.00 |
| gpt-4.1-mini | $0.40 | $1.60 |
| gpt-4.1-nano | $0.10 | $0.40 |
| o3 | $2.00 | $8.00 |
| o4-mini / o3-mini | $1.10 | $4.40 |

Unrecognised deployments fall back to GPT-4o pricing ($2.50/$10.00).

### Security Rule

The `/api/convert` endpoint in `main.py` silently blocks the AI plugin when `classification` is `Confidential` or `Restricted`. Documents with these classifications must not be sent to external AI services.

---

## file_analyzer.py

**Purpose:** Pre-conversion file analysis. Scans a file to detect images, merged cells, and sheet count, then returns a recommendation for conversion mode.

**Entry point:** `analyze_file(file_path, original_filename)` → `FileAnalysisResponse`

**Depends on:** `openpyxl`, `pdfplumber`, `zipfile` (for DOCX/PPTX)

**Used by:** `app/main.py` — `/api/analyze` endpoint

### Analysis by File Type

| Type | What is checked |
|------|----------------|
| Excel | `ws._images` per sheet, `ws.merged_cells.ranges`, sheet count |
| PDF | Page image objects via `pdfplumber` |
| DOCX / PPTX | Image files inside ZIP archive (`word/media/` or `ppt/media/`) |

### Recommendation Values

| Value | Condition |
|-------|-----------|
| `standard` | No images, or only merged cells |
| `ai_recommended` | Images found but text content also present |
| `ai_required` | Scanned PDF (very low text-to-page ratio) |

---

## data_cleaner.py

**Purpose:** Apply AI-readability transforms to a previously converted Markdown file, producing a version a knowledge base / RAG pipeline can consume directly. Does not mutate disk — callers persist the result via `/api/clean/save`.

**Entry point:** `clean_markdown(raw_markdown, *, document_id, classification, options)` → `CleanResult`

**Depends on:** `pdfplumber` (optional, for PDF table recovery), `openai.AzureOpenAI` (optional, for the AI summary), `app.config`, `app.models`

**Used by:** `app/main.py` — `/api/clean` and `/api/clean/save` endpoints

### Pipeline (each step is a toggle in `CleanOptions`)

| Step | What it does |
|------|------|
| `relative_paths` | Rewrites absolute Windows paths in the YAML frontmatter to repo-relative (`data/local/...`) |
| `skip_hidden_sheets` | Drops `## Sheet:` blocks whose `sheet_state` is not `visible` or whose name matches an admin/data/lookup pattern |
| `promote_glossary` | Pulls sheets named Information / Definitions / Glossary / Notes into a top-level `## Glossary` block so terms sit beside the data that uses them |
| `split_multi_block_sheets` | Splits sheets that contain multiple tables separated by blank rows into `Sheet (block N)` sub-sections |
| `pivot_to_long_form` | Detects Plan × Procedure-Code Excel pivot grids and replaces the wide pivot table with a long-form fact table (one row per Plan × Procedure × Coverage). Refuses to reshape if the procedure-code count does not exactly match the data-column count, and reports the mismatch in the cleaning log. |
| `collapse_repeated_runs` | When a Markdown table row has 5+ identical values from column 2 onward, replaces them with a one-line summary (e.g. `Full cover (×41 columns)`) |
| `pdf_extract_tables` | For PDF source documents, runs `pdfplumber.extract_tables()` over the saved raw PDF and appends any recovered tables under a `## Recovered Tables` section. The original text output stays in place. |
| `normalize_whitespace` | Trims trailing whitespace, collapses blank-line runs, normalizes non-breaking spaces |
| `ai_summary` | Asks Azure OpenAI for a 3-sentence summary of the document. **Blocked for `Confidential` / `Restricted` classifications** — the block is enforced both inside the cleaner and at the API boundary in `main.py`. |

### Output

Returns a `CleanResult` containing the cleaned Markdown, a `cleaning_log` of `CleaningStep` entries (one per pipeline step, with `applied` flag and human-readable note), an optional `summary` string, an optional `summary_blocked_reason`, and the raw vs. cleaned size in characters.

---

## rag_service.py

**Purpose:** Turn the converted Markdown corpus into an answerable knowledge base — chunk it, retrieve over it, and have Azure OpenAI answer strictly from what was retrieved.

**Entry point:** `answer_question(question, *, history, top_k, mode)` → `ChatResponse`

**Depends on:** `app.config`, `app.models`, `openai.AzureOpenAI` (generation only), `graph_rag` (lazily, when `mode="graph"`)

**Used by:** `app/main.py` — `/api/chat`, `/api/knowledge/*`, `/api/chat/feedback`; also `graph_builder` and `graph_rag`, which reuse its chunks and tokenizer

### Indexing

| Stage | Behaviour |
|------|------|
| Split | On ATX headings first, then into overlapping ~1400-character windows so a table is not cut mid-row |
| Chunk id | `{document_id}#{n}` — stable across rebuilds, and the same id the graph uses as a node |
| Tokenize | Lowercase → drop stopwords → conservative stemmer |
| Cache | Held in memory and rebuilt only when the corpus fingerprint changes |

### Retrieval

Ranked by BM25, with two deliberate departures from the textbook:

- **Prefix expansion.** A query term absent from the index expands by prefix, so *physio* reaches *physiotherapy* without a synonym list.
- **Gate on coverage, not score.** IDF collapses on a corpus this small, which makes the BM25 score useless as an absolute relevance threshold. The gate is the fraction of query terms actually present in the retrieved passages.

### Grounded generation

Below the coverage gate the model is **never called** — the fallback answer is returned instead, which is what stops the system inventing a benefit that does not exist. Above it, Azure OpenAI answers as JSON restricted to the retrieved passages, and the reported confidence is capped by what retrieval actually supports.

`mode` selects the retriever: `core` for BM25 alone, `graph` to hand the seeds to `graph_rag` for expansion and re-ranking. If `graph` is asked for and no snapshot exists, the call falls back to `core` and records it in `retrieval.fell_back` rather than failing the question.

---

## graph_builder.py

**Purpose:** Compile the converted corpus into a knowledge graph. Derived entirely from the Markdown the converter already produced — no hand-authored ontology, no supplied vocabulary, and no model call anywhere in the module. Given the same Markdown it produces the same graph, which is what makes a retrieval result reproducible months later.

**Entry point:** `build_graph(index)` → the snapshot dict written to `data/knowledge_graph.json`

**Depends on:** `rag_service` (chunks, index, stopwords, stemmer)

**Used by:** `backend/scripts/build_knowledge_graph.py`

### Node types

| Type | What it is |
|------|------|
| `document` | One converted file, carrying its governance metadata |
| `section` | One ATX heading and the body under it |
| `chunk` | One retrieval unit — the passage the retriever actually scores |
| `concept` | A multi-word domain term mined from the corpus |
| `value` | A money amount or a percentage |
| `constraint` | A frequency limit, waiting period or age bound |
| `community` | A cluster of concepts that keep occurring together |
| `facet` | A governance value: department, classification, access group |

### Relations

| Relation | Direction | Source of the fact |
|------|------|------|
| `contains` | document → section | The file's own outline |
| `has_chunk` | section → chunk | Which passages came from which section |
| `follows` | chunk → chunk | Reading order inside a document |
| `mentions` | chunk → concept/value/constraint | Term extraction, weighted by TF-IDF salience |
| `co_occurs` | concept ↔ concept | NPMI over co-mention |
| `in_community` | concept → community | Label propagation |
| `similar_to` | chunk ↔ chunk | TF-IDF cosine, mostly cross-document |
| `governed_by` | document → facet | Manifest metadata |

### The three techniques

1. **C-value term extraction** (Frantzi, Ananiadou & Mima, 2000) mines the concept vocabulary. It favours multi-word terms and discounts a short term that mostly appears inside a longer one, so *restorative treatment* survives and the bare *treatment* that only ever appears inside it does not.
2. **Normalised pointwise mutual information** ranks concept pairs, so `co_occurs` records association rather than mere frequency. Without it every common term links to every other common term and the graph says nothing.
3. **Label propagation** (Raghavan, Albert & Kumara, 2007) finds concept communities. Near-linear, no parameter to tune, and made deterministic here by processing nodes in a fixed order and breaking label ties lexicographically.

Every threshold that shapes the graph is a module-level constant, and the method is recorded inside the snapshot under `method` — so a graph can always explain how it was made.

---

## graph_store.py

**Purpose:** Load the compiled snapshot and make it walkable. Thin by design: the file on disk is the source of truth, no extraction logic lives here (that is `graph_builder`) and no retrieval policy (that is `graph_rag`).

**Entry point:** `load_graph(path=None)` → `KnowledgeGraph`; `try_load_graph()` for callers that treat an absent graph as a normal state

**Depends on:** `app.config` (`KNOWLEDGE_GRAPH_PATH`)

**Used by:** `app/main.py` (`/api/graph/knowledge`), `graph_rag`

| Concern | Behaviour |
|------|------|
| Caching | Re-reads only when the file's mtime **or** size changes, so a rewrite inside the mtime resolution is still caught |
| Edge weights | Each relation stores a different quantity — TF-IDF salience, cosine, NPMI — normalised here into one comparable `(0, 1]` number |
| Fan-out cap | Neighbours are returned strongest-first and capped at 12 per relation. A concept like *waiting period* appears in dozens of chunks; following every one turns retrieval into a corpus scan, so a step through a hub costs what a step through a specific term costs |
| Failure | `GraphUnavailable` when the file is missing, unparseable, or has no nodes — never a partial graph |

---

## graph_rag.py

**Purpose:** Graph-expansion retrieval. Lexical scoring rates each passage on its own, which fails in a specific, repeatable way when the answer is not inside one passage — a figure and its qualifier in adjacent chunks, an answer assembled from two documents, or a question naming a concept where the passage names only an instance. Each of those is a connection the corpus already contains and a bag-of-words score cannot see.

**Entry point:** `expand(question, seeds, graph, ...)` → ranked `GraphHit` list plus a trace

**Depends on:** `graph_store`, `rag_service` (`Hit`, `_tokenize`)

**Used by:** `rag_service.answer_question` when `mode="graph"`

### Stages

1. **Seed** with the BM25 pass (top 8). Lexical retrieval is good at finding a starting point, so it is kept rather than replaced — the graph is an expansion stage, not a competing retriever.
2. **Link** the question's terms to concept nodes, so a question naming a concept can enter the graph even where no single passage scores well.
3. **Propagate** relevance outward for 2 hops along typed edges, with a per-relation weight and a 0.55 per-hop decay. Two hops is what the useful patterns need — `chunk → concept → chunk` for a shared term, `chunk → section → chunk` for a sibling passage — and a third mostly adds noise.
4. **Fuse** normalised lexical (weight 0.6) with normalised graph (0.4) and re-rank.

`contains` and `governed_by` carry weight 0 deliberately: a document node is a true fact about the corpus and belongs in the picture, but stepping through it would make every passage in a document relevant to every other, which is not retrieval. They stay visible and unwalked.

### The limit it respects

Expansion never opens the coverage gate. If no seed passage clears the threshold, the answer is still the fallback — a graph walk that starts from nothing relevant arrives at nothing relevant, and letting it produce context anyway would reintroduce exactly the hallucination risk the gate exists to remove.

Every stage is recorded in a trace, so a returned passage can always be explained as "seeded by BM25 at rank 3" or "reached from chunk 12 through the concept *waiting period*" — and the same trace is what the graph view replays.
