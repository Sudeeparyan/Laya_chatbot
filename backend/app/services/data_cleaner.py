"""AI-readability cleaner for converted Markdown.

Takes the raw Markdown produced by `markdown_converter.convert_upload` and
applies a pipeline of deterministic transforms that make the output easier for
an LLM (and a human reviewer) to understand:

- Skip hidden / admin sheets so the index does not pick up junk content.
- Reshape pivot-style Excel sheets (Plan x Procedure-Code) into long-form
  rows (one fact per row).
- Collapse repeated runs of identical cells (e.g. 41 columns of "Full cover")
  into a single human-readable summary.
- Promote glossary / definitions / information sheets to a top-level
  ``## Glossary`` section so terms are co-located with the data that uses them.
- Split sheets that contain multiple table blocks separated by blank rows.
- Normalize whitespace and remove trailing pipes from empty Markdown columns.
- Rewrite absolute Windows paths in the YAML frontmatter to repo-relative
  paths.
- (PDF) re-extract tables with ``pdfplumber.extract_tables`` so cells stay
  associated with their headers instead of being flattened to paragraphs.
- (Optional) ask Azure OpenAI for a 3-sentence summary of the document. This
  step is silently disabled for ``Confidential`` / ``Restricted`` documents.

The cleaner does NOT mutate the original Markdown on disk. Callers receive a
``CleanResult`` and may persist it through ``/api/clean/save`` (handled in
``main.py``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import (
    AZURE_CHAT_DEPLOYMENT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    PROJECT_ROOT,
    RAW_UPLOAD_DIR,
)
from app.models import CleaningStep, CleanOptions, CleanResult


HIDDEN_SHEET_PATTERN = re.compile(
    r"^(admin|data|lookup|temp|hidden|raw|sandbox|backup|debug)\b",
    re.IGNORECASE,
)
SHEET_HEADING = re.compile(r"^## Sheet:\s+(.*?)\s*$", re.MULTILINE)
GLOSSARY_SHEET_NAMES = {
    "information",
    "info",
    "definitions",
    "definition",
    "glossary",
    "key",
    "legend",
    "notes",
    "readme",
    "instructions",
}


@dataclass
class _ParsedDoc:
    frontmatter: dict[str, Any]
    title: str
    additional_context: str | None
    body: str  # everything after the title + additional context block


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def clean_markdown(
    raw_markdown: str,
    *,
    document_id: str,
    classification: str,
    options: CleanOptions,
) -> CleanResult:
    """Run the cleaning pipeline. Pure function — no disk writes."""
    parsed = _parse_markdown(raw_markdown)
    log: list[CleaningStep] = []

    body = parsed.body
    frontmatter = dict(parsed.frontmatter)

    if options.relative_paths:
        frontmatter, step = _relativize_paths(frontmatter)
        log.append(step)

    if options.skip_hidden_sheets:
        body, step = _skip_hidden_sheets(body, frontmatter)
        log.append(step)

    if options.promote_glossary:
        body, step = _promote_glossary(body)
        log.append(step)

    if options.split_multi_block_sheets:
        body, step = _split_multi_block_sheets(body)
        log.append(step)

    if options.pivot_to_long_form:
        body, step = _pivot_to_long_form(body)
        log.append(step)

    if options.collapse_repeated_runs:
        body, step = _collapse_repeated_runs(body)
        log.append(step)

    if options.pdf_extract_tables and frontmatter.get("source_type", "").upper() == "PDF":
        body, step = _reflow_pdf_tables(body, frontmatter)
        log.append(step)

    if options.normalize_whitespace:
        body, step = _normalize_whitespace(body)
        log.append(step)

    summary: str | None = None
    summary_blocked: str | None = None
    clean_ai_cost = None
    if options.ai_summary:
        summary, step, summary_blocked, clean_ai_cost = _ai_summary(
            body=body,
            frontmatter=frontmatter,
            classification=classification,
        )
        log.append(step)

    cleaned = _rebuild_markdown(frontmatter, parsed.title, parsed.additional_context, body, summary)

    return CleanResult(
        document_id=document_id,
        cleaned_markdown=cleaned,
        cleaning_log=log,
        summary=summary,
        summary_blocked_reason=summary_blocked,
        raw_size=len(raw_markdown),
        cleaned_size=len(cleaned),
        clean_ai_cost=clean_ai_cost,
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_markdown(raw: str) -> _ParsedDoc:
    """Split the doc into frontmatter, title, optional context section, body."""
    text = raw.lstrip("\ufeff").lstrip()
    frontmatter: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            yaml_block = text[3:end].strip("\n")
            body = text[end + 4 :].lstrip("\n")
            frontmatter = _parse_simple_yaml(yaml_block)

    title = ""
    title_match = re.match(r"#\s+(.+?)\s*\n", body)
    if title_match:
        title = title_match.group(1).strip()
        body = body[title_match.end() :]

    additional_context: str | None = None
    ctx_match = re.match(
        r"##\s+Additional Context Provided by User\s*\n(.*?)(?=\n##\s|\Z)",
        body,
        re.DOTALL,
    )
    if ctx_match:
        additional_context = ctx_match.group(1).strip() or None
        body = body[ctx_match.end() :]

    return _ParsedDoc(
        frontmatter=frontmatter,
        title=title,
        additional_context=additional_context,
        body=body.lstrip("\n"),
    )


def _parse_simple_yaml(block: str) -> dict[str, Any]:
    """Parse the small subset of YAML that ``markdown_converter`` writes.

    The producer emits one of:
      - ``key: <json-value>`` (string, number, bool)
      - ``key:`` followed by ``  - <json-value>`` lines
    Anything else is preserved verbatim under a string key.
    """
    out: dict[str, Any] = {}
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][\w]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, raw_val = m.group(1), m.group(2)
        if raw_val == "":
            items: list[Any] = []
            j = i + 1
            while j < len(lines) and lines[j].startswith("  - "):
                items.append(_load_json_or_str(lines[j][4:]))
                j += 1
            out[key] = items
            i = j
        else:
            out[key] = _load_json_or_str(raw_val)
            i += 1
    return out


def _load_json_or_str(value: str) -> Any:
    value = value.strip()
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


# ---------------------------------------------------------------------------
# Cleaning steps
# ---------------------------------------------------------------------------


def _relativize_paths(frontmatter: dict[str, Any]) -> tuple[dict[str, Any], CleaningStep]:
    rewritten = 0
    project_root_str = str(PROJECT_ROOT)
    for key in ("source_path", "markdown_path"):
        value = frontmatter.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.replace("\\", "/")
        root_normalized = project_root_str.replace("\\", "/")
        if normalized.lower().startswith(root_normalized.lower()):
            frontmatter[key] = normalized[len(root_normalized) :].lstrip("/")
            rewritten += 1
    return frontmatter, CleaningStep(
        id="relative_paths",
        label="Rewrote absolute paths to repo-relative",
        applied=rewritten > 0,
        note=f"{rewritten} path field(s) rewritten" if rewritten else "All paths already relative",
        metrics={"rewritten": rewritten},
    )


def _split_sheet_sections(body: str) -> list[tuple[str, str]]:
    """Return list of (sheet_name, section_text). Includes a leading None-named
    'preamble' section if body has content before the first ``## Sheet:`` line.
    """
    parts: list[tuple[str, str]] = []
    matches = list(SHEET_HEADING.finditer(body))
    if not matches:
        return [("", body)]
    if matches[0].start() > 0:
        parts.append(("", body[: matches[0].start()].rstrip()))
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        parts.append((match.group(1).strip(), body[start:end].rstrip()))
    return parts


def _join_sheet_sections(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(section for _, section in sections if section.strip())


def _is_hidden_sheet(name: str, frontmatter: dict[str, Any]) -> bool:
    if not name:
        return False
    sheets_meta = frontmatter.get("sheets") or []
    for entry in sheets_meta:
        if isinstance(entry, dict) and entry.get("sheet_name") == name:
            if entry.get("sheet_state") and entry["sheet_state"] != "visible":
                return True
            if entry.get("status") == "empty":
                return True
    return bool(HIDDEN_SHEET_PATTERN.match(name.strip()))


def _skip_hidden_sheets(body: str, frontmatter: dict[str, Any]) -> tuple[str, CleaningStep]:
    sections = _split_sheet_sections(body)
    if len(sections) <= 1 and not sections[0][0]:
        return body, CleaningStep(
            id="skip_hidden_sheets",
            label="Skipped hidden / admin sheets",
            applied=False,
            note="No sheet structure detected",
        )
    skipped: list[str] = []
    kept: list[tuple[str, str]] = []
    for name, text in sections:
        if name and _is_hidden_sheet(name, frontmatter):
            skipped.append(name)
            continue
        kept.append((name, text))
    return _join_sheet_sections(kept), CleaningStep(
        id="skip_hidden_sheets",
        label="Skipped hidden / admin sheets",
        applied=bool(skipped),
        note=("Removed: " + ", ".join(skipped)) if skipped else "No hidden sheets found",
        metrics={"skipped_count": len(skipped), "skipped": skipped},
    )


def _promote_glossary(body: str) -> tuple[str, CleaningStep]:
    sections = _split_sheet_sections(body)
    if len(sections) <= 1 and not sections[0][0]:
        return body, CleaningStep(
            id="promote_glossary",
            label="Promoted definitions to a top-level Glossary",
            applied=False,
            note="No sheet structure detected",
        )
    promoted: list[str] = []
    glossary_blocks: list[str] = []
    remaining: list[tuple[str, str]] = []
    for name, text in sections:
        normalized = name.strip().lower()
        if normalized in GLOSSARY_SHEET_NAMES:
            glossary_blocks.append(_to_glossary_block(name, text))
            promoted.append(name)
        else:
            remaining.append((name, text))
    if not glossary_blocks:
        return body, CleaningStep(
            id="promote_glossary",
            label="Promoted definitions to a top-level Glossary",
            applied=False,
            note="No definitions / information sheet detected",
        )
    glossary_md = "## Glossary\n\n" + "\n\n".join(glossary_blocks).strip()
    new_body = glossary_md + "\n\n" + _join_sheet_sections(remaining)
    return new_body, CleaningStep(
        id="promote_glossary",
        label="Promoted definitions to a top-level Glossary",
        applied=True,
        note="Promoted: " + ", ".join(promoted),
        metrics={"promoted": promoted},
    )


def _to_glossary_block(name: str, section_text: str) -> str:
    """Convert an ``## Sheet: Information`` section into a glossary block."""
    lines = section_text.splitlines()
    cleaned_lines: list[str] = [f"### {name}"]
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## Sheet:"):
            continue
        if stripped.startswith("- Source rows") or stripped.startswith("- Source columns") or stripped.startswith("- Header row"):
            continue
        if stripped.startswith("### Sheet Notes") or stripped.startswith("### Table"):
            in_table = stripped.startswith("### Table")
            continue
        if in_table and stripped.startswith("|"):
            term, *definition = [cell.strip() for cell in stripped.strip("|").split("|")]
            joined_def = " — ".join(d for d in definition if d and d != "---")
            if not term and not joined_def:
                continue
            if set(term) <= {"-", " "}:
                continue
            if joined_def:
                cleaned_lines.append(f"- **{term}** — {joined_def}")
            else:
                cleaned_lines.append(f"- {term}")
        elif stripped:
            cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines).strip()


def _split_multi_block_sheets(body: str) -> tuple[str, CleaningStep]:
    sections = _split_sheet_sections(body)
    new_sections: list[tuple[str, str]] = []
    split_count = 0
    for name, text in sections:
        if not name:
            new_sections.append((name, text))
            continue
        blocks = _detect_table_blocks(text)
        if len(blocks) <= 1:
            new_sections.append((name, text))
            continue
        for idx, block_md in enumerate(blocks, start=1):
            new_sections.append((f"{name} (block {idx})", block_md))
        split_count += len(blocks) - 1
    return _join_sheet_sections(new_sections), CleaningStep(
        id="split_multi_block_sheets",
        label="Split sheets that contained multiple table blocks",
        applied=split_count > 0,
        note=f"Created {split_count} extra block section(s)" if split_count else "No multi-block sheets detected",
        metrics={"new_blocks": split_count},
    )


def _detect_table_blocks(section_text: str) -> list[str]:
    """Best-effort: a ``### Table`` followed by long blank gaps becomes blocks.

    Conservative — we only split when at least one of the resulting blocks has
    its own data rows.
    """
    table_match = re.search(r"### Table\s*\n", section_text)
    if not table_match:
        return [section_text]
    head = section_text[: table_match.end()]
    body = section_text[table_match.end() :]
    rows = body.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    blank_run = 0
    for row in rows:
        if row.strip() == "":
            blank_run += 1
            if blank_run >= 2 and current and any(r.strip().startswith("|") for r in current):
                blocks.append(current)
                current = []
                blank_run = 0
            continue
        blank_run = 0
        current.append(row)
    if current:
        blocks.append(current)
    if len(blocks) <= 1:
        return [section_text]
    out: list[str] = []
    for block_lines in blocks:
        if not any(line.strip().startswith("|") for line in block_lines):
            continue
        out.append(head + "\n".join(block_lines).strip())
    return out or [section_text]


def _pivot_to_long_form(body: str) -> tuple[str, CleaningStep]:
    """Detect cross-tab Plan × Procedure-Code Excel sheets and replace the
    pivot table with a long-form fact table (one row per Plan-Procedure pair).

    The original pivot grid is summarized in a short collapsible block so the
    cleaned output stays substantially smaller than the raw output but a
    reviewer can still see what it looked like.

    The reshape ONLY runs when the number of procedure-code tokens in the
    sheet notes exactly matches the number of data columns in the table —
    otherwise an off-by-one would mis-label every cell, so we skip and report.
    """
    sections = _split_sheet_sections(body)
    converted: list[str] = []
    skipped: list[str] = []
    new_sections: list[tuple[str, str]] = []
    for name, text in sections:
        if not name:
            new_sections.append((name, text))
            continue
        long_form_section, status = _maybe_long_form(name, text)
        if status == "converted" and long_form_section:
            converted.append(name)
            new_sections.append((name, long_form_section))
        else:
            if status == "mismatch":
                skipped.append(name)
            new_sections.append((name, text))
    note_parts: list[str] = []
    if converted:
        note_parts.append("Reshaped: " + ", ".join(converted))
    if skipped:
        note_parts.append(
            "Skipped (codes/columns count mismatch — kept original): " + ", ".join(skipped)
        )
    return _join_sheet_sections(new_sections), CleaningStep(
        id="pivot_to_long_form",
        label="Reshaped pivot tables into AI-friendly long form",
        applied=bool(converted),
        note=" | ".join(note_parts) if note_parts else "No pivot-style sheets detected",
        metrics={"reshaped": converted, "skipped": skipped},
    )


def _maybe_long_form(sheet_name: str, section_text: str) -> tuple[str | None, str]:
    """Return (replacement_section, status).

    status is one of:
      - ``converted``  — pivot reshape succeeded; replacement_section replaces
        the entire ``## Sheet:`` block.
      - ``mismatch``   — pivot detected but column count != code count, so we
        leave the original block alone.
      - ``not_pivot``  — sheet doesn't look like a pivot, leave it alone.
    """
    notes_match = re.search(r"### Sheet Notes\s*\n((?:- .+\n?)+)", section_text)
    table_match = re.search(r"### Table\s*\n((?:\|.*\n?)+)", section_text)
    if not notes_match or not table_match:
        return None, "not_pivot"

    note_lines = [line[2:].strip() for line in notes_match.group(1).splitlines() if line.startswith("- ")]
    code_row: list[str] | None = None
    for note in note_lines:
        candidates = [token.strip() for token in note.split("|") if token.strip()]
        if len(candidates) >= 5 and sum(1 for c in candidates if _looks_like_code(c)) >= len(candidates) * 0.6:
            code_row = candidates
            break
    if not code_row:
        return None, "not_pivot"

    table_rows = [line for line in table_match.group(1).splitlines() if line.strip().startswith("|")]
    if len(table_rows) < 4:
        return None, "not_pivot"

    parsed_rows: list[list[str]] = []
    for raw in table_rows:
        cells = [cell.strip() for cell in raw.strip().strip("|").split("|")]
        parsed_rows.append(cells)

    header = parsed_rows[0]
    data_rows = [r for r in parsed_rows[2:] if any(c for c in r)]
    if len(header) < 3 or len(data_rows) < 2:
        return None, "not_pivot"

    code_columns = [i for i, _ in enumerate(header) if i >= 2]
    if len(code_row) != len(code_columns):
        # Off-by-one would silently corrupt the long-form output. Refuse.
        return None, "mismatch"

    width = len(header)
    aligned_codes = [""] * width
    for offset, code in enumerate(code_row):
        col = 2 + offset
        if col < width:
            aligned_codes[col] = code

    long_rows: list[tuple[str, str, str, str, str]] = []
    for row in data_rows:
        cells = (row + [""] * width)[:width]
        plan_code = cells[0]
        plan_name = cells[1]
        if not plan_name:
            continue
        for col in code_columns:
            cov = cells[col]
            if not cov:
                continue
            procedure_code = aligned_codes[col]
            description = header[col]
            long_rows.append((plan_code, plan_name, procedure_code, description, cov))

    if not long_rows:
        return None, "not_pivot"

    # Build the replacement section: keep the sheet heading + source-range
    # bullets, drop the original wide table, and emit the long-form fact table.
    sheet_intro_match = re.match(r"(## Sheet:.+?)(?=###\s)", section_text, re.DOTALL)
    intro = sheet_intro_match.group(1).rstrip() if sheet_intro_match else f"## Sheet: {sheet_name}"
    plan_count = len({(r[0], r[1]) for r in long_rows})
    code_count = len({r[2] for r in long_rows if r[2]})

    lines = [
        intro,
        "",
        "### AI-friendly fact table",
        "",
        f"_Reshaped from a {plan_count}-plan × {code_count}-procedure pivot grid into one fact per row._",
        "",
        "| Plan code | Plan name | Hospital | Procedure code | Procedure | Coverage |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for plan_code, plan_name, procedure_code, description, cov in long_rows:
        lines.append(
            "| {pc} | {pn} | {hosp} | {code} | {desc} | {cov} |".format(
                pc=_md_escape(plan_code),
                pn=_md_escape(plan_name),
                hosp=_md_escape(sheet_name),
                code=_md_escape(procedure_code),
                desc=_md_escape(description),
                cov=_md_escape(cov),
            )
        )
    return "\n".join(lines), "converted"


def _looks_like_code(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9\-_/.]{1,15}", token.strip()))


def _md_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _collapse_repeated_runs(body: str) -> tuple[str, CleaningStep]:
    """For each Markdown table row: if all cells from column 2 onward share a
    single value, replace the body with a one-line summary.
    """
    lines = body.splitlines()
    out: list[str] = []
    collapsed_rows = 0
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            out.append(line)
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            out.append(line)
            continue
        head = cells[:2]
        tail = cells[2:]
        non_empty_tail = [c for c in tail if c]
        unique_tail = set(non_empty_tail)
        if len(unique_tail) == 1 and len(non_empty_tail) >= 5:
            single_value = next(iter(unique_tail))
            replacement = f"| {head[0]} | {head[1]} | {single_value} (×{len(non_empty_tail)} columns) |"
            out.append(replacement)
            collapsed_rows += 1
        else:
            out.append(line)
    return "\n".join(out), CleaningStep(
        id="collapse_repeated_runs",
        label="Collapsed repeated-value runs",
        applied=collapsed_rows > 0,
        note=f"Collapsed {collapsed_rows} row(s) of identical values" if collapsed_rows else "No long repeated runs found",
        metrics={"collapsed_rows": collapsed_rows},
    )


def _normalize_whitespace(body: str) -> tuple[str, CleaningStep]:
    original = body
    body = body.replace("\u00a0", " ")
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = re.sub(r"^[ \t]+(\|)", r"\1", body, flags=re.MULTILINE)
    return body.strip() + "\n", CleaningStep(
        id="normalize_whitespace",
        label="Normalized whitespace",
        applied=body != original,
        note="Trimmed whitespace and collapsed blank lines",
    )


def _reflow_pdf_tables(body: str, frontmatter: dict[str, Any]) -> tuple[str, CleaningStep]:
    """Re-extract tables from the source PDF using ``pdfplumber.extract_tables``
    and append them as a ``## Recovered Tables`` section. The original text
    output stays in place.
    """
    source_rel = frontmatter.get("source_path")
    if not isinstance(source_rel, str):
        return body, CleaningStep(
            id="pdf_extract_tables",
            label="Re-extracted tables from PDF using pdfplumber",
            applied=False,
            note="No source_path in frontmatter",
        )
    candidate = (PROJECT_ROOT / source_rel).resolve() if not Path(source_rel).is_absolute() else Path(source_rel)
    if not candidate.exists():
        candidate = RAW_UPLOAD_DIR / Path(source_rel).name
    if not candidate.exists():
        return body, CleaningStep(
            id="pdf_extract_tables",
            label="Re-extracted tables from PDF using pdfplumber",
            applied=False,
            note=f"Raw PDF not found at {source_rel}",
        )

    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return body, CleaningStep(
            id="pdf_extract_tables",
            label="Re-extracted tables from PDF using pdfplumber",
            applied=False,
            note="pdfplumber not installed",
        )

    tables_md: list[str] = []
    table_count = 0
    try:
        with pdfplumber.open(str(candidate)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_tables = page.extract_tables() or []
                for tbl_idx, table in enumerate(page_tables, start=1):
                    rendered = _render_pdf_table(table)
                    if rendered:
                        table_count += 1
                        tables_md.append(
                            f"### Page {page_num} — Table {tbl_idx}\n\n{rendered}"
                        )
    except Exception as exc:  # pragma: no cover - defensive
        return body, CleaningStep(
            id="pdf_extract_tables",
            label="Re-extracted tables from PDF using pdfplumber",
            applied=False,
            note=f"pdfplumber failed: {exc}",
        )

    if not tables_md:
        return body, CleaningStep(
            id="pdf_extract_tables",
            label="Re-extracted tables from PDF using pdfplumber",
            applied=False,
            note="No tables detected in source PDF",
        )

    appended = body.rstrip() + "\n\n## Recovered Tables\n\n" + "\n\n".join(tables_md) + "\n"
    return appended, CleaningStep(
        id="pdf_extract_tables",
        label="Re-extracted tables from PDF using pdfplumber",
        applied=True,
        note=f"Recovered {table_count} table(s) and appended as a new section",
        metrics={"table_count": table_count},
    )


def _render_pdf_table(table: list[list[str | None]]) -> str:
    rows = [[(cell or "").strip() for cell in row] for row in table if any(c for c in row)]
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [(r + [""] * width)[:width] for r in rows]
    header = rows[0]
    data = rows[1:]
    lines = [
        "| " + " | ".join(_md_escape(c) or " " for c in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in data:
        lines.append("| " + " | ".join(_md_escape(c) or " " for c in row) + " |")
    return "\n".join(lines)


def _ai_summary(
    *,
    body: str,
    frontmatter: dict[str, Any],
    classification: str,
) -> "tuple[str | None, CleaningStep, str | None, AICostEstimate | None]":
    """Generate a 3-sentence summary using Azure OpenAI.

    Blocked for Confidential/Restricted documents. The block is enforced here
    AND mirrored at the API boundary in main.py.

    Returns ``(summary_text, step, blocked_reason, cost_estimate)``.
    """
    from app.models import AICostEstimate, AICostCall
    from app.services.ai_plugin import _get_azure_client, _get_pricing

    classification_normalized = (classification or "Internal").strip()
    if classification_normalized in {"Confidential", "Restricted"}:
        return None, CleaningStep(
            id="ai_summary",
            label="AI-generated 3-sentence summary",
            applied=False,
            note=f"Blocked: classification is {classification_normalized}",
        ), f"AI summary disabled for {classification_normalized} documents.", None

    if not (AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY):
        return None, CleaningStep(
            id="ai_summary",
            label="AI-generated 3-sentence summary",
            applied=False,
            note="Azure OpenAI not configured",
        ), None, None

    title = frontmatter.get("document_title") or "this document"
    source_type = frontmatter.get("source_type") or "document"
    department = frontmatter.get("department") or "unspecified department"
    additional_context = frontmatter.get("additional_context") or ""
    snippet = body[:8000]

    prompt = (
        "You are summarizing a business document for a knowledge base. "
        "Write exactly 3 short sentences in plain English. Sentence 1: what the "
        "document is and which team it serves. Sentence 2: the most useful facts "
        "or rules it contains. Sentence 3: when a user should consult it. Do not "
        "invent facts that are not in the content.\n\n"
        f"Title: {title}\nSource type: {source_type}\nDepartment: {department}\n"
        f"User-provided context: {additional_context}\n\n"
        f"Content:\n{snippet}"
    )

    try:
        client = _get_azure_client()
        response = client.chat.completions.create(
            model=AZURE_CHAT_DEPLOYMENT,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=240,
            temperature=1,
        )
        text = (response.choices[0].message.content or "").strip()

        # Capture actual token usage for cost
        cost: AICostEstimate | None = None
        if hasattr(response, "usage") and response.usage:
            inp = getattr(response.usage, "prompt_tokens", 0) or 0
            out = getattr(response.usage, "completion_tokens", 0) or 0
            deployment = AZURE_CHAT_DEPLOYMENT or "gpt-4o"
            inp_per_1m, out_per_1m = _get_pricing(deployment)
            total_cost = inp / 1_000_000 * inp_per_1m + out / 1_000_000 * out_per_1m
            cost = AICostEstimate(
                model=deployment,
                total_input_tokens=inp,
                total_output_tokens=out,
                total_cost_usd=round(total_cost, 6),
                cost_per_call=[
                    AICostCall(
                        call_type="ai_summary",
                        location="AI-generated 3-sentence summary",
                        model=deployment,
                        input_tokens=inp,
                        output_tokens=out,
                        total_cost_usd=round(total_cost, 6),
                    )
                ],
            )
    except Exception as exc:
        return None, CleaningStep(
            id="ai_summary",
            label="AI-generated 3-sentence summary",
            applied=False,
            note=f"Azure OpenAI call failed: {exc}",
        ), None, None

    if not text:
        return None, CleaningStep(
            id="ai_summary",
            label="AI-generated 3-sentence summary",
            applied=False,
            note="Empty response from Azure OpenAI",
        ), None, None

    return text, CleaningStep(
        id="ai_summary",
        label="AI-generated 3-sentence summary",
        applied=True,
        note="Inserted near the top of the cleaned Markdown",
    ), None, cost


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


def _rebuild_markdown(
    frontmatter: dict[str, Any],
    title: str,
    additional_context: str | None,
    body: str,
    summary: str | None,
) -> str:
    yaml_block = _dump_simple_yaml(frontmatter)
    parts: list[str] = ["---", yaml_block, "---", ""]
    if title:
        parts.append(f"# {title}")
        parts.append("")
    if summary:
        parts.append("## AI Summary")
        parts.append("")
        parts.append(summary.strip())
        parts.append("")
    if additional_context:
        parts.append("## Additional Context Provided by User")
        parts.append("")
        parts.append(additional_context.strip())
        parts.append("")
    parts.append(body.strip())
    parts.append("")
    return "\n".join(parts)


def _dump_simple_yaml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if value is None or value == []:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append("  - " + json.dumps(item, ensure_ascii=False))
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines)
