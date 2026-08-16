# Data Schema

## Manifest File (JSON)

Every conversion produces a JSON manifest saved to `data/local/manifests/{document_id}.json`. This is the source of truth for document-level metadata.

### Full Schema

```json
{
  "document_id": "cardiac-list-jan-26-4b6458edcc3e",
  "document_title": "Cardiac list - Jan 26",
  "source_filename": "Cardiac list - Jan 26.xlsx",
  "source_type": "XLSX",
  "source_path": "data/local/raw_uploads/cardiac-list-jan-26-4b6458edcc3e.xlsx",
  "markdown_path": "data/local/markdown_outputs/cardiac-list-jan-26-4b6458edcc3e.md",
  "file_sha256": "4b6458edcc3e...",
  "file_size_bytes": 45230,
  "converted_at_utc": "2026-06-10T09:00:00+00:00",
  "converter": "openpyxl",
  "extraction_strategy": "openpyxl workbook extraction with sheet-level Markdown tables",
  "department": "Claims",
  "access_group": "KH_CLAIMS_USERS",
  "classification": "Internal",
  "document_owner": "Claims Ops",
  "additional_context": "Category: Cardiac benefit list. Missing columns are not supplied by the source workbook.",
  "version": "v1.0",
  "expiry_review_date": "2026-12-01",
  "sheet_count": 6,
  "sheets": [
    {
      "name": "Mater Private",
      "row_count": 120,
      "column_count": 45,
      "header_row": 3,
      "has_merged_cells": true,
      "is_hidden": false
    }
  ]
}
```

### Field Reference

| Field | Type | Purpose |
|-------|------|---------|
| `document_id` | string | Stable unique ID: `{slug}-{sha256[:12]}`. Used for all saved filenames. |
| `document_title` | string | Human-readable title and citation label |
| `source_filename` | string | Original uploaded filename |
| `source_type` | string | Uppercase file extension: `XLSX`, `PDF`, `DOCX`, etc. |
| `source_path` | string | Path to saved raw source file |
| `markdown_path` | string | Path to saved Markdown output |
| `file_sha256` | string | SHA-256 hash of the raw file (for deduplication and change detection) |
| `file_size_bytes` | int | File size in bytes |
| `converted_at_utc` | string | ISO 8601 UTC timestamp of conversion |
| `converter` | string | `openpyxl` or `markitdown` |
| `extraction_strategy` | string | Human-readable description of how the file was converted |
| `department` | string\|null | Department filter for future retrieval |
| `access_group` | string\|null | Future Entra/security group filter |
| `classification` | string | `General`, `Internal`, `Confidential`, or `Restricted` |
| `document_owner` | string\|null | Business owner responsible for content |
| `additional_context` | string\|null | User-provided category, description, business rules |
| `version` | string\|null | Document version string |
| `expiry_review_date` | string\|null | ISO date for lifecycle control |
| `sheet_count` | int\|null | Number of sheets (Excel only) |
| `sheets` | array | Per-sheet metadata (Excel only) |
| `cleaning_log` | array | (added by `/api/clean/save`) Per-step audit of the AI-readability cleaner. Each entry: `id`, `label`, `applied`, `note`, `metrics`. |
| `ai_summary` | string\|null | (added by `/api/clean/save`) 3-sentence Azure OpenAI summary of the document, or `null` if disabled / blocked / unavailable. |
| `raw_markdown_path` | string\|null | (added by `/api/clean/save`) Path to the pre-clean Markdown backup (`{document_id}.raw.md`). |

---

## Markdown Frontmatter

Every Markdown output starts with YAML frontmatter identical to the manifest fields. This makes the file self-describing and allows future chunking pipelines to extract metadata without consulting the manifest separately.

```yaml
---
document_id: "cardiac-list-jan-26-4b6458edcc3e"
document_title: "Cardiac list - Jan 26"
source_filename: "Cardiac list - Jan 26.xlsx"
source_type: "XLSX"
department: "Claims"
access_group: "KH_CLAIMS_USERS"
classification: "Internal"
document_owner: "Claims Ops"
additional_context: "Category: Cardiac benefit list."
version: "v1.0"
expiry_review_date: "2026-12-01"
file_sha256: "4b6458edcc3e..."
converted_at_utc: "2026-06-10T09:00:00+00:00"
extraction_strategy: "openpyxl workbook extraction with sheet-level Markdown tables"
---
```

---

## Confidence Score Object

Returned in every `ConversionResponse` and logged in the manifest.

```json
{
  "overall": 78.5,
  "structure_fidelity": 85.0,
  "content_completeness": 90.0,
  "image_handling": 0.0,
  "formatting_preservation": 70.0,
  "factors": [
    "Merged cells detected: structure may be approximated"
  ],
  "limitations_hit": [
    "Excel contains embedded images that are NOT extracted"
  ]
}
```

| Score Range | Label | Meaning |
|-------------|-------|---------|
| 85–100 | High Confidence | Output is reliable for AI consumption |
| 65–84 | Medium Confidence | Output is usable but review flagged issues |
| 0–64 | Low Confidence | Output has significant gaps; manual review needed |

---

## AI Cost Estimate Object

Returned in `ConversionResponse.ai_cost` when the AI plugin was used. `null` when no AI calls were made.

```json
{
  "model": "gpt-4o",
  "total_input_tokens": 3420,
  "total_output_tokens": 1280,
  "total_cost_usd": 0.021350,
  "pricing_source": "Azure OpenAI pay-as-you-go Global Standard (USD)",
  "cost_per_call": [
    {
      "call_type": "image_analysis",
      "location": "Sheet1/B5",
      "model": "gpt-4o",
      "input_tokens": 1710,
      "output_tokens": 640,
      "total_cost_usd": 0.010675
    }
  ]
}
```

Token usage is captured from the actual `response.usage` returned by the Azure OpenAI API — it is **not** estimated.

`FileAnalysisResponse` includes two optional fields for pre-conversion estimates:

| Field | Type | Description |
|-------|------|-------------|
| `estimated_ai_cost_usd` | float \| null | Rough cost if AI plugin is run (based on image count × avg tokens) |
| `estimated_ai_cost_note` | string \| null | Human-readable explanation of the estimate |

---

## File Storage Layout

```
data/
└── local/
    ├── raw_uploads/           # Original uploaded files
    │   └── {document_id}.{ext}
    ├── markdown_outputs/      # Converted Markdown files
    │   └── {document_id}.md
    └── manifests/             # JSON metadata per document
        └── {document_id}.json
```

---

## Future Chunk Schema (Phase 2)

When this project moves to Phase 2 (chunking and embeddings), each chunk record should extend the manifest metadata with:

| Field | Type | Purpose |
|-------|------|---------|
| `chunk_id` | string | Deterministic: `{document_id}-chunk-{n}` |
| `content` | string | Chunk text content |
| `sheet_name` | string\|null | Source sheet (Excel) |
| `page_number` | int\|null | Source page (PDF) |
| `row_start` | int\|null | First source row in chunk |
| `row_end` | int\|null | Last source row in chunk |
| `section_heading` | string\|null | Nearest Markdown heading above the chunk |
| `status` | string | `active`, `expired`, `draft`, `deprecated` |
| `effective_date` | string\|null | Date from which chunk content is valid |
| `embedding` | float[] | Vector embedding (Azure OpenAI) |
