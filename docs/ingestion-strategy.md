# Ingestion Strategy for Mixed Files

## Current Decision

Use Markdown as the inspection and review format, then index chunk records derived from that Markdown. This approach works because Markdown is readable by humans, easy to diff, and compact enough for LLM context. The important production rule is that Markdown content must travel with metadata; plain text alone is not enough for reliable retrieval, citations, security trimming, or expiry handling.

The current app implements the first slice:

1. Upload a source file.
2. Save the raw source locally.
3. Convert to Markdown.
4. Save a metadata manifest.
5. Preview the result before future embedding.

## Document Metadata Schema

Store this once per source document and copy the relevant fields onto each chunk.

| Field | Type | Filterable | Purpose |
|---|---:|---:|---|
| `document_id` | string | yes | Stable source document id, based on title/hash or source system id |
| `document_title` | string | yes | Human citation title |
| `source_filename` | string | yes | Original file name |
| `source_type` | string | yes | PDF, XLSX, DOCX, CSV, etc. |
| `source_path` | string | yes | Blob path, SharePoint URL, or local path |
| `markdown_path` | string | yes | Path to converted Markdown artifact |
| `file_sha256` | string | yes | Change detection and deduplication |
| `department` | string | yes | Department filter, for example HR or Claims |
| `access_group` | string or string[] | yes | Entra/security group mapping |
| `classification` | string | yes | General, Internal, Confidential, Restricted |
| `document_owner` | string | yes | Accountable owner |
| `additional_context` | string | yes | User-provided category, description, missing-field explanation, or business context |
| `version` | string | yes | Version shown in citations and freshness ranking |
| `effective_date` | date | yes | Date content becomes valid |
| `expiry_review_date` | date | yes | Review or expiry date |
| `status` | string | yes | draft, approved, active, expired, deprecated |
| `created_at` | datetime | yes | Ingestion creation timestamp |
| `last_modified` | datetime | yes | Source modified timestamp |
| `converted_at_utc` | datetime | yes | Conversion timestamp |

## Chunk Schema for Azure AI Search or Vector DB

This is the shape to embed and index later.

```json
{
  "chunk_id": "cardiac-list-jan-26-abc123::sheet-Mater-Private::row-12-28",
  "document_id": "cardiac-list-jan-26-abc123",
  "content": "Markdown text for this chunk",
  "content_vector": [0.01, 0.02],
  "content_type": "table_rows",
  "document_title": "Cardiac list - Jan 26",
  "department": "Claims",
  "access_group": ["KH_CLAIMS_USERS"],
  "classification": "Internal",
  "additional_context": "Category: Cardiac benefit list. Missing columns are not supplied by the source workbook.",
  "source_type": "XLSX",
  "source_path": "...",
  "markdown_path": "...",
  "sheet_name": "Mater Private",
  "page_number": null,
  "section_heading": "Cardiac procedures",
  "row_start": 12,
  "row_end": 28,
  "column_headers": ["Code", "Plan", "Cover"],
  "version": "v1.0",
  "effective_date": "2026-01-01",
  "expiry_review_date": "2026-12-01",
  "status": "active",
  "file_sha256": "...",
  "chunk_index": 4
}
```

## File-Type Strategy

| File type | Conversion strategy | Chunking strategy |
|---|---|---|
| Excel | Preserve workbook, sheet name, inferred header row, row/column coordinates, merged-cell context | Chunk by logical table and row groups; include headers in every chunk |
| CSV | Convert to Markdown table and retain header/row numbers | Chunk by row groups; repeat headers |
| PDF | Use MarkItDown for searchable PDFs; use Document Intelligence OCR/layout for scanned or layout-heavy PDFs | Chunk by heading/page/paragraph; cite page numbers |
| Word | Convert headings, paragraphs, lists, tables | Chunk by section heading and table blocks |
| PowerPoint | Convert slide title, text, notes | Chunk by slide |
| Images/scans | Use OCR before Markdown | Chunk by page/region with confidence scores |

## Excel Corner Cases

Handle these before embedding:

| Case | Risk | Recommended handling |
|---|---|---|
| Blank leading rows/columns | `Unnamed` headers and empty context | Trim outer blank rows/columns before header detection |
| Merged cells | Lost category labels | Fill merged ranges from the anchor cell during extraction |
| Multiple tables on one sheet | Wrong headers for later tables | Detect table blocks separated by blank rows in a later iteration |
| Formula cells | Missing values if cached result is unavailable | Extract cached values; warn when formulas return blank |
| Hidden sheets/rows | Accidental indexing of admin content | Record hidden state and decide whether to skip by policy |
| Very wide tables | Poor chunks and high token use | Chunk by row groups and keep headers in every chunk |
| Codes, plan names, dates | Vector-only search misses exact values | Use hybrid retrieval and keyword fields for exact matching |

## Retrieval Strategy

Use hybrid retrieval: keyword search plus vector search. This matches Microsoft guidance for RAG because vector search catches semantic matches, while keyword search catches exact codes, dates, names, and specialist terms.

Recommended query flow:

1. Resolve user identity and allowed groups.
2. Build a strict metadata filter before retrieval:
   `department in user_departments`, `access_group in user_groups`, `status eq 'active'`, and `expiry_review_date ge today` or null.
3. Run hybrid search with the user question and its embedding.
4. Use semantic reranking where available.
5. Boost exact matches for procedure codes, plan names, document titles, and sheet names.
6. Return only the selected chunks to the LLM.
7. Require citations from `document_title`, `page_number`, `sheet_name`, `row_start`, `row_end`, or `section_heading`.

For Azure AI Search vector filters, use `preFilter` for security and expiry filters when recall matters. Test `postFilter` only when filters are broad and latency is a bigger concern. Do not rely on prompt rules to enforce access.

## Expired or Deprecated Document Handling

Use both soft controls and hard cleanup.

| Layer | Action |
|---|---|
| Metadata | Keep `status`, `effective_date`, `expiry_review_date`, `version`, and `supersedes_document_id` |
| Query time | Always filter out expired, deprecated, draft, or unauthorized chunks before LLM context assembly |
| Scheduled job | Nightly job marks expired chunks inactive or deletes them from the vector/search index |
| Source storage | If using Azure Blob indexers, use blob soft delete or custom metadata deletion detection |
| Push indexing | If using a custom pipeline, delete by `document_id` or deterministic `chunk_id` |
| Audit | Keep a small manifest/history record even after chunks are removed |

Recommended policy: soft-delete first by setting `status = 'expired'` or `is_active = false`, verify queries exclude it, then hard-delete stale chunks after the retention window.

## Azure AI Search Index Fields

Minimum fields for the future index:

| Field | Attributes |
|---|---|
| `chunk_id` | key, filterable |
| `content` | searchable, retrievable |
| `content_vector` | searchable vector |
| `document_id` | filterable, facetable |
| `document_title` | searchable, filterable, retrievable |
| `department` | filterable, facetable |
| `access_group` | filterable |
| `classification` | filterable, facetable |
| `source_type` | filterable, facetable |
| `source_path` | retrievable |
| `markdown_path` | retrievable |
| `page_number` | filterable, retrievable |
| `sheet_name` | searchable, filterable, retrievable |
| `row_start`, `row_end` | retrievable |
| `section_heading` | searchable, filterable, retrievable |
| `version` | filterable, retrievable |
| `effective_date`, `expiry_review_date` | filterable, sortable |
| `status` | filterable, facetable |
| `file_sha256` | filterable |

## Why This Approach Works

The approach is sound if the conversion layer is treated as a controlled ingestion stage. Markdown improves reviewability and LLM grounding, but retrieval quality will come from consistent chunking, metadata filters, source citations, exact-match fields, semantic vectors, and lifecycle rules. The current MVP proves the first step: convert real Excel/PDF/Office files into inspectable Markdown and save the source-linked metadata needed for the next indexing stage.