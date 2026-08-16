import json
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import (
    AZURE_CHAT_DEPLOYMENT,
    FEEDBACK_LOG,
    MANIFEST_DIR,
    MARKDOWN_OUTPUT_DIR,
    MAX_UPLOAD_BYTES,
    ensure_data_dirs,
)
from app.models import (
    CapabilitiesResponse,
    CapabilityItem,
    ChatRequest,
    ChatResponse,
    CleanOptions,
    CleanResult,
    CleanSaveRequest,
    CleanSaveResponse,
    CleaningStep,
    ConversionResponse,
    FeedbackRequest,
    FeedbackResponse,
    FileAnalysisResponse,
    KnowledgeOverview,
)
from app.services.markdown_converter import convert_upload
from app.services.ai_plugin import is_plugin_available, estimate_cost_for_images, _get_pricing
from app.services.file_analyzer import analyze_file
from app.services.data_cleaner import clean_markdown
from app.services import graph_store, rag_service
from app.services.graph_builder import corpus_fingerprint


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_data_dirs()
    yield


app = FastAPI(title="Knowledge Hub Markdown Converter", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    # Vite falls back to 5174, 5175… when its default port is taken, and a
    # hard-coded port list turns that into an unexplained "failed to fetch".
    # Any loopback origin is acceptable for this locally-run app.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/pricing")
def pricing_info() -> dict:
    """Return the active deployment name and token pricing so the frontend can show correct cost estimates."""
    deployment = AZURE_CHAT_DEPLOYMENT or "gpt-chat-latest"
    inp_per_1m, out_per_1m = _get_pricing(deployment)
    return {
        "deployment": deployment,
        "input_per_1m_tokens_usd": inp_per_1m,
        "output_per_1m_tokens_usd": out_per_1m,
        "source": "Azure OpenAI pay-as-you-go Global Standard (USD)",
    }


# ---------------------------------------------------------------------------
# Knowledge Hub chat (RAG over the converted Markdown corpus)
# ---------------------------------------------------------------------------


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    """Answer an agent question using only the converted Markdown knowledge base.

    ``mode`` picks the retriever: ``core`` for BM25 alone, ``graph`` to expand
    those hits over the compiled knowledge graph before re-ranking. Both are
    answered from retrieved text only.
    """
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty.")
    try:
        return rag_service.answer_question(
            question,
            history=payload.history,
            top_k=payload.top_k,
            mode=payload.mode,
        )
    except Exception as exc:  # pragma: no cover - defensive surface
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc


@app.get("/api/knowledge/overview", response_model=KnowledgeOverview)
def knowledge_overview() -> KnowledgeOverview:
    """Documents, corpus size and starter prompts for the chat page."""
    return KnowledgeOverview(
        stats=rag_service.knowledge_stats(),
        documents=rag_service.list_documents(),
        suggestions=rag_service.suggested_questions(),
    )


@app.get("/api/knowledge/documents/{document_id}")
def knowledge_document(document_id: str) -> dict[str, str]:
    """Return the stored Markdown for one document so a source can be read in full."""
    md_path = _resolve_markdown_path(document_id)
    if md_path is None:
        raise HTTPException(status_code=404, detail=f"No document found for '{document_id}'.")
    return {"document_id": document_id, "markdown": md_path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------------------
# Knowledge graph (the structure retrieval walks)
# ---------------------------------------------------------------------------


@app.get("/api/graph/knowledge")
def knowledge_graph() -> dict:
    """Return the compiled knowledge graph.

    Eight node types and eight relations, all derived from the converted corpus
    — see ``app/services/graph_builder.py``. This is the same snapshot the
    graph-expansion retriever walks, so the picture and the retrieval are never
    two different things.

    A ``stale`` flag is added when the snapshot was compiled from a corpus other
    than the one currently indexed, which happens after an upload. Rebuild with
    ``python backend/scripts/build_knowledge_graph.py``.
    """
    try:
        graph = graph_store.load_graph()
    except graph_store.GraphUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    index = rag_service.build_index()
    payload = dict(graph.raw)
    payload["stale"] = bool(index.docs) and graph.fingerprint != corpus_fingerprint(index)
    payload["indexed_corpus_fingerprint"] = corpus_fingerprint(index)
    return payload


@app.post("/api/chat/feedback", response_model=FeedbackResponse)
def chat_feedback(payload: FeedbackRequest) -> FeedbackResponse:
    """Record a thumbs up/down on an answer (PRD 3.7)."""
    rating = payload.rating.strip().lower()
    if rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'.")
    record = payload.model_dump()
    record["rating"] = rating
    record["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        rag_service.record_feedback(record)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write feedback: {exc}") from exc
    return FeedbackResponse(recorded=True, log_path=str(FEEDBACK_LOG))


@app.post("/api/convert", response_model=ConversionResponse)
async def convert_document(
    file: UploadFile = File(...),
    document_title: str | None = Form(default=None),
    department: str | None = Form(default=None),
    access_group: str | None = Form(default=None),
    classification: str = Form(default="Internal"),
    document_owner: str | None = Form(default=None),
    additional_context: str | None = Form(default=None),
    version: str | None = Form(default=None),
    expiry_review_date: str | None = Form(default=None),
    enable_plugin: str = Form(default="false"),
) -> ConversionResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    # Block AI plugin for Confidential/Restricted classifications
    plugin_requested = enable_plugin.lower() in ("true", "1", "yes")
    classification_normalized = (classification or "Internal").strip()
    if plugin_requested and classification_normalized in ("Confidential", "Restricted"):
        plugin_requested = False  # Silently disable - response will note this

    suffix = Path(file.filename).suffix
    temp_path: Path | None = None

    try:
        bytes_written = 0
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := await file.read(1024 * 1024):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Uploaded file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                    )
                temp_file.write(chunk)

        if bytes_written == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        return convert_upload(
            temp_path,
            original_filename=file.filename,
            document_title=document_title,
            department=department,
            access_group=access_group,
            classification=classification,
            document_owner=document_owner,
            additional_context=additional_context,
            version=version,
            expiry_review_date=expiry_review_date,
            enable_plugin=plugin_requested,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - FastAPI surface
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@app.post("/api/analyze", response_model=FileAnalysisResponse)
async def analyze_document(
    file: UploadFile = File(...),
    classification: str = Form(default="Internal"),
) -> FileAnalysisResponse:
    """Pre-analyze a file to detect images and recommend conversion mode."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    suffix = Path(file.filename).suffix
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := await file.read(1024 * 1024):
                temp_file.write(chunk)

        classification_normalized = (classification or "Internal").strip()
        ai_blocked = classification_normalized in ("Confidential", "Restricted")

        result = analyze_file(temp_path, file.filename)

        if ai_blocked:
            result.ai_blocked = True
            result.ai_blocked_reason = (
                f"AI plugin is disabled for '{classification_normalized}' documents. "
                "Sensitive data must not be sent to external AI services."
            )
            if result.recommendation in ("ai_recommended", "ai_required"):
                result.recommendation_reason += (
                    f" However, AI is blocked for '{classification_normalized}' classification."
                )
        else:
            # Provide pre-conversion cost estimate when images are detected
            if result.has_images and result.image_count > 0:
                est_cost = estimate_cost_for_images(result.image_count)
                result.estimated_ai_cost_usd = est_cost
                result.estimated_ai_cost_note = (
                    f"Estimated AI cost for {result.image_count} image(s): "
                    f"~${est_cost:.5f} USD — based on GPT-4o pay-as-you-go pricing."
                )

        return result
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@app.get("/api/outputs/{filename}")
def download_output(filename: str) -> FileResponse:
    path = (MARKDOWN_OUTPUT_DIR / filename).resolve()
    if path.parent != MARKDOWN_OUTPUT_DIR.resolve() or not path.exists() or path.suffix.lower() != ".md":
        raise HTTPException(status_code=404, detail="Markdown output not found.")
    return FileResponse(path, media_type="text/markdown", filename=path.name)


@app.post("/api/clean", response_model=CleanResult)
def clean_document(
    document_id: str = Form(...),
    classification: str = Form(default="Internal"),
    options: str | None = Form(default=None),
) -> CleanResult:
    """Run the AI-readability cleaner against a previously converted document.

    Reads the canonical Markdown from ``data/local/markdown_outputs/{document_id}.md``
    (or its ``.raw.md`` backup if one exists from a previous save) and returns
    a cleaned version. Does NOT mutate disk — call ``/api/clean/save`` to
    persist.
    """
    md_path = _resolve_markdown_path(document_id)
    if md_path is None:
        raise HTTPException(status_code=404, detail=f"No converted Markdown found for document_id '{document_id}'.")

    # Security gate: AI summary must be blocked for Confidential/Restricted at the API boundary
    classification_normalized = (classification or "Internal").strip()
    parsed_options = _parse_clean_options(options)
    ai_blocked_at_boundary = False
    if classification_normalized in ("Confidential", "Restricted") and parsed_options.ai_summary:
        parsed_options.ai_summary = False
        ai_blocked_at_boundary = True

    raw_markdown = md_path.read_text(encoding="utf-8")
    try:
        result = clean_markdown(
            raw_markdown,
            document_id=document_id,
            classification=classification_normalized,
            options=parsed_options,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=422, detail=f"Cleaning failed: {exc}") from exc

    if ai_blocked_at_boundary:
        result.summary_blocked_reason = (
            f"AI summary disabled for {classification_normalized} documents — "
            "blocked at the API boundary."
        )
        result.cleaning_log.append(
            CleaningStep(
                id="ai_summary",
                label="AI-generated 3-sentence summary",
                applied=False,
                note=f"Blocked: classification is {classification_normalized}",
            )
        )
    return result


@app.post("/api/clean/save", response_model=CleanSaveResponse)
def save_cleaned_document(payload: CleanSaveRequest) -> CleanSaveResponse:
    """Promote the cleaned Markdown to canonical.

    - Backs the current canonical file up to ``{document_id}.raw.md`` (only if
      no backup exists yet — we never overwrite the original raw conversion).
    - Writes the cleaned Markdown to ``{document_id}.md``.
    - Records ``cleaning_log`` and ``ai_summary`` in the manifest JSON.
    """
    md_path = _resolve_markdown_path(payload.document_id)
    if md_path is None:
        raise HTTPException(status_code=404, detail=f"No converted Markdown found for document_id '{payload.document_id}'.")

    canonical_path = MARKDOWN_OUTPUT_DIR / f"{payload.document_id}.md"
    raw_backup_path = MARKDOWN_OUTPUT_DIR / f"{payload.document_id}.raw.md"
    if not raw_backup_path.exists() and canonical_path.exists():
        raw_backup_path.write_text(canonical_path.read_text(encoding="utf-8"), encoding="utf-8")
    canonical_path.write_text(payload.cleaned_markdown, encoding="utf-8")

    manifest_path = MANIFEST_DIR / f"{payload.document_id}.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
        manifest["cleaning_log"] = [step.model_dump() for step in payload.cleaning_log]
        manifest["ai_summary"] = payload.summary
        manifest["raw_markdown_path"] = str(raw_backup_path)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return CleanSaveResponse(
        document_id=payload.document_id,
        canonical_path=str(canonical_path),
        raw_backup_path=str(raw_backup_path),
        manifest_path=str(manifest_path),
    )


def _resolve_markdown_path(document_id: str) -> Path | None:
    safe_id = Path(document_id).name
    if safe_id != document_id or not safe_id:
        return None
    candidate = MARKDOWN_OUTPUT_DIR / f"{safe_id}.md"
    return candidate if candidate.exists() else None


def _parse_clean_options(raw: str | None) -> CleanOptions:
    if not raw:
        return CleanOptions()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid options JSON: {exc}") from exc
    try:
        return CleanOptions.model_validate(data)
    except Exception as exc:  # pragma: no cover - pydantic raises ValidationError
        raise HTTPException(status_code=400, detail=f"Invalid options: {exc}") from exc


@app.get("/api/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    """Return what this converter can and cannot do."""
    plugin_available = is_plugin_available()

    supported = [
        CapabilityItem(
            feature="Excel (.xlsx/.xls) text & tables",
            supported=True,
            details="Extracts all sheets as Markdown tables with headers, handles merged cells (approximated).",
        ),
        CapabilityItem(
            feature="PDF text extraction",
            supported=True,
            details="Extracts text content and basic table structures from PDFs.",
        ),
        CapabilityItem(
            feature="Word (.docx) conversion",
            supported=True,
            details="Extracts text, headings, lists, and tables. Embedded images referenced.",
        ),
        CapabilityItem(
            feature="PowerPoint (.pptx) conversion",
            supported=True,
            details="Extracts slide text, tables, and chart data.",
        ),
        CapabilityItem(
            feature="CSV / JSON / XML / HTML",
            supported=True,
            details="High-fidelity text extraction for structured text formats.",
        ),
        CapabilityItem(
            feature="Image descriptions (AI Plugin)",
            supported=plugin_available,
            details="Uses Azure OpenAI GPT to describe embedded images in documents." if plugin_available
            else "Requires Azure OpenAI configuration. Images are currently silently dropped.",
            plugin_required=True,
        ),
        CapabilityItem(
            feature="Scanned PDF / OCR (AI Plugin)",
            supported=plugin_available,
            details="Uses AI vision to extract text from scanned/image-based PDFs." if plugin_available
            else "Not available without AI plugin. Scanned PDFs will produce empty/minimal output.",
            plugin_required=True,
        ),
        CapabilityItem(
            feature="Confidence scoring",
            supported=True,
            details="Every conversion returns a confidence score (0-100) indicating reliability of output.",
        ),
    ]

    limitations = [
        CapabilityItem(
            feature="Excel embedded images",
            supported=False,
            details="Images/shapes in Excel cells are silently dropped. Enable AI plugin for descriptions.",
            plugin_required=True,
        ),
        CapabilityItem(
            feature="Excel cell formatting",
            supported=False,
            details="Currency symbols, date formats, colors, conditional formatting are not preserved.",
        ),
        CapabilityItem(
            feature="Excel merged cells",
            supported=True,
            details="Merged cells are expanded but original visual layout context may be approximated.",
        ),
        CapabilityItem(
            feature="PDF complex tables",
            supported=False,
            details="Multi-level headers, nested tables, or tables spanning pages may misalign columns.",
        ),
        CapabilityItem(
            feature="PDF images/charts",
            supported=False,
            details="Images and charts in PDFs are not extracted without AI plugin.",
            plugin_required=True,
        ),
        CapabilityItem(
            feature="Scanned/image-only PDFs",
            supported=False,
            details="Produces empty output without AI plugin OCR.",
            plugin_required=True,
        ),
        CapabilityItem(
            feature="Document track changes",
            supported=False,
            details="Word track changes, comments, and revision history are not preserved.",
        ),
        CapabilityItem(
            feature="Slide animations/transitions",
            supported=False,
            details="PowerPoint animations, transitions, and exact slide layouts are not preserved.",
        ),
        CapabilityItem(
            feature="Password-protected files",
            supported=False,
            details="Cannot open or convert password-protected documents.",
        ),
    ]

    file_types = sorted([
        ".xlsx", ".xls", ".xlsm", ".csv", ".pdf", ".docx", ".doc", ".pptx",
        ".html", ".htm", ".json", ".xml", ".txt", ".md", ".epub",
        ".msg", ".rtf", ".zip",
    ])

    return CapabilitiesResponse(
        supported=supported,
        limitations=limitations,
        file_types=file_types,
        max_file_size_mb=MAX_UPLOAD_BYTES // (1024 * 1024),
        plugin_available=plugin_available,
    )