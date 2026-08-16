"""
Batch converter — processes all documents in raw_uploads through the full
Knowledge Hub Markdown Converter + Data Cleaner pipeline.

Usage:
    python batch_convert.py [--source FOLDER] [--no-clean] [--no-ai]

Source default: data/local/raw_uploads
Outputs:       data/local/markdown_outputs/ and data/local/manifests/

Pipeline per file:
  1. convert_upload()  → raw Markdown + metadata + confidence scoring
  2. clean_markdown()  → hidden-sheet removal, glossary promotion, pivot reshape,
                         PDF table re-extraction, whitespace normalization,
                         path relativization, optional AI summary
  3. Write cleaned .md to output folder
"""

import argparse
import sys
import time
from pathlib import Path

# Ensure backend packages are importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import (
    ensure_data_dirs,
    MARKDOWN_OUTPUT_DIR,
    MANIFEST_DIR,
    RAW_UPLOAD_DIR,
    PROJECT_ROOT,
)
from app.models import CleanOptions
from app.services.markdown_converter import convert_upload
from app.services.data_cleaner import clean_markdown

SUPPORTED_EXTENSIONS = {
    ".xlsx", ".xlsm", ".xltx", ".xltm",  # Excel (openpyxl path)
    ".xls",                                # Legacy Excel (MarkItDown)
    ".csv", ".doc", ".docx", ".epub",
    ".html", ".htm", ".json", ".md",
    ".msg", ".pdf", ".pptx", ".rtf",
    ".txt", ".xml", ".zip",
}


# ---------------------------------------------------------------------------
# DOCUMENT METADATA
#
# Every converted file is stamped with the same governance metadata, which is
# written into the Markdown frontmatter and the manifest so downstream
# retrieval can filter on it. Per-document titles and context are set through
# the Documents tab in the UI, which is where a human is already reviewing the
# conversion; this CLI exists to get a folder converted in bulk, not to curate
# it. Override the defaults below for a different department or access group.
# ---------------------------------------------------------------------------

DEFAULT_METADATA: dict[str, str | None] = {
    "department": "Claims",
    "access_group": "KH_CLAIMS_USERS",
    "classification": "Internal",
    "document_owner": "Customer Care",
    "additional_context": None,
}


# ---------------------------------------------------------------------------
# Main batch logic
# ---------------------------------------------------------------------------


def batch_convert(
    source_folder: Path,
    *,
    run_cleaner: bool = True,
    enable_ai: bool = True,
    ai_summary: bool = True,
) -> dict:
    """Convert all supported files in source_folder with metadata. Returns summary."""
    ensure_data_dirs()

    if not source_folder.exists():
        print(f"ERROR: Source folder not found: {source_folder}")
        sys.exit(1)

    files = sorted(
        f for f in source_folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    skipped = sorted(
        f.name for f in source_folder.iterdir()
        if f.is_file() and f.suffix.lower() not in SUPPORTED_EXTENSIONS
    )

    print(f"\n{'='*70}")
    print(f"  KNOWLEDGE HUB - BATCH MARKDOWN CONVERSION")
    print(f"{'='*70}")
    print(f"  Source folder  : {source_folder}")
    print(f"  Output folder  : {MARKDOWN_OUTPUT_DIR}")
    print(f"  Manifest folder: {MANIFEST_DIR}")
    print(f"  Files to convert: {len(files)}")
    print(f"  AI Plugin      : {'ON' if enable_ai else 'OFF'}")
    print(f"  Data Cleaner   : {'ON' if run_cleaner else 'OFF'}")
    print(f"  AI Summary     : {'ON' if ai_summary else 'OFF'}")
    if skipped:
        print(f"  Skipped (unsupported): {len(skipped)}")
    print(f"{'='*70}\n")

    clean_options = CleanOptions(
        skip_hidden_sheets=True,
        pivot_to_long_form=True,
        collapse_repeated_runs=True,
        promote_glossary=True,
        split_multi_block_sheets=True,
        normalize_whitespace=True,
        relative_paths=True,
        pdf_extract_tables=True,
        ai_summary=ai_summary,
    )

    results = {"success": [], "failed": [], "skipped": skipped}
    start_all = time.time()

    for i, filepath in enumerate(files, 1):
        filename = filepath.name
        meta = DEFAULT_METADATA

        # Determine if AI plugin should be used for this file
        ext = filepath.suffix.lower()
        use_plugin = enable_ai and ext in {".pdf", ".docx", ".pptx"}

        print(f"  [{i:02d}/{len(files):02d}] {filename}")
        print(f"           Title: {meta.get('document_title', filepath.stem)}")
        print(f"           Dept: {meta.get('department', 'Claims')} | AI: {'Yes' if use_plugin else 'No'}")
        t0 = time.time()

        try:
            response = convert_upload(
                filepath,
                original_filename=filename,
                document_title=meta.get("document_title"),
                department=meta.get("department"),
                access_group=meta.get("access_group"),
                classification=meta.get("classification", "Internal"),
                document_owner=meta.get("document_owner"),
                additional_context=meta.get("additional_context"),
                version=meta.get("version"),
                expiry_review_date=meta.get("expiry_review_date"),
                enable_plugin=use_plugin,
            )
            elapsed_convert = time.time() - t0

            # Run cleaner pass
            clean_note = ""
            if run_cleaner:
                t1 = time.time()
                clean_result = clean_markdown(
                    response.markdown,
                    document_id=response.metadata.document_id,
                    classification=meta.get("classification", "Internal"),
                    options=clean_options,
                )
                output_path = Path(response.output_file)
                output_path.write_text(clean_result.cleaned_markdown, encoding="utf-8")
                elapsed_clean = time.time() - t1
                reduction = (
                    (clean_result.raw_size - clean_result.cleaned_size)
                    / max(clean_result.raw_size, 1) * 100
                )
                clean_note = f" | Cleaned: {reduction:+.0f}% ({elapsed_clean:.1f}s)"

            conf = response.confidence.overall
            plugin_note = f" | Plugin: {response.plugin_name}" if response.plugin_used else ""
            print(f"           Done: Confidence {conf:.0f}% | {elapsed_convert:.1f}s{plugin_note}{clean_note}")

            if response.warnings:
                important = [w for w in response.warnings if "empty" not in w.lower() and "sheet" not in w.lower()[:10]]
                if important:
                    print(f"           Note: {important[0][:90]}")

            results["success"].append({
                "file": filename,
                "document_id": response.metadata.document_id,
                "title": meta.get("document_title", filepath.stem),
                "confidence": conf,
                "plugin_used": response.plugin_used,
                "output": response.output_file,
            })
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"           FAILED ({elapsed:.1f}s): {exc}")
            results["failed"].append({"file": filename, "error": str(exc)})

        print()

    total_time = time.time() - start_all

    # Final summary
    print(f"{'='*70}")
    print(f"  CONVERSION COMPLETE")
    print(f"{'='*70}")
    print(f"  Successful     : {len(results['success'])}/{len(files)}")
    print(f"  Failed         : {len(results['failed'])}/{len(files)}")
    print(f"  Total time     : {total_time:.1f}s")
    if results["success"]:
        avg_conf = sum(r["confidence"] for r in results["success"]) / len(results["success"])
        ai_count = sum(1 for r in results["success"] if r["plugin_used"])
        print(f"  Avg confidence : {avg_conf:.1f}%")
        print(f"  AI-enhanced    : {ai_count}/{len(results['success'])}")
    print(f"\n  Markdown outputs: {MARKDOWN_OUTPUT_DIR}")
    print(f"  Manifests:        {MANIFEST_DIR}")
    print(f"{'='*70}\n")

    if results["failed"]:
        print("  FAILED FILES:")
        for f in results["failed"]:
            print(f"    - {f['file']}: {f['error']}")
        print()

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Knowledge Hub - Batch convert documents to clean Markdown for RAG"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=RAW_UPLOAD_DIR,
        help="Source folder (default: data/local/raw_uploads)",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip the data cleaning pass",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable Azure OpenAI plugin (faster, no cost)",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Disable AI-generated document summaries",
    )
    args = parser.parse_args()

    batch_convert(
        args.source,
        run_cleaner=not args.no_clean,
        enable_ai=not args.no_ai,
        ai_summary=not (args.no_ai or args.no_summary),
    )
