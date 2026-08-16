from typing import Any

from pydantic import BaseModel, Field


class ConversionMetadata(BaseModel):
    document_id: str
    document_title: str
    source_filename: str
    source_type: str
    source_path: str
    markdown_path: str
    file_sha256: str
    file_size_bytes: int
    converted_at_utc: str
    converter: str
    extraction_strategy: str
    department: str | None = None
    access_group: str | None = None
    classification: str = "Internal"
    document_owner: str | None = None
    additional_context: str | None = None
    version: str | None = None
    expiry_review_date: str | None = None
    sheet_count: int | None = None
    sheets: list[dict[str, Any]] = Field(default_factory=list)


class ConfidenceScore(BaseModel):
    overall: float = Field(description="Overall confidence 0-100")
    structure_fidelity: float = Field(description="Table/structure accuracy 0-100")
    content_completeness: float = Field(description="Content completeness 0-100")
    image_handling: float = Field(description="Image content coverage 0-100")
    formatting_preservation: float = Field(description="Formatting preservation 0-100")
    factors: list[str] = Field(default_factory=list, description="Factors affecting score")
    limitations_hit: list[str] = Field(default_factory=list, description="Known limitations encountered")


class ConversionResponse(BaseModel):
    markdown: str
    metadata: ConversionMetadata
    output_file: str
    manifest_file: str
    warnings: list[str] = Field(default_factory=list)
    confidence: ConfidenceScore = Field(default_factory=lambda: ConfidenceScore(
        overall=0, structure_fidelity=0, content_completeness=0,
        image_handling=100, formatting_preservation=0
    ))
    plugin_used: bool = False
    plugin_name: str | None = None
    ai_cost: "AICostEstimate | None" = None


class CapabilityItem(BaseModel):
    feature: str
    supported: bool
    details: str
    plugin_required: bool = False


class CapabilitiesResponse(BaseModel):
    supported: list[CapabilityItem]
    limitations: list[CapabilityItem]
    file_types: list[str]
    max_file_size_mb: int
    plugin_available: bool


class FileAnalysisResponse(BaseModel):
    """Pre-conversion analysis of an uploaded file."""
    filename: str
    extension: str
    file_size_bytes: int
    has_images: bool
    image_count: int
    has_merged_cells: bool
    sheet_count: int | None = None
    recommendation: str  # "standard" | "ai_recommended" | "ai_required"
    recommendation_reason: str
    ai_blocked: bool = False
    ai_blocked_reason: str | None = None
    estimated_ai_cost_usd: float | None = None
    estimated_ai_cost_note: str | None = None


class CleaningStep(BaseModel):
    """One step of the AI-readability cleaner."""
    id: str
    label: str
    applied: bool
    note: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class CleanOptions(BaseModel):
    """Toggles for the Clean Data tab."""
    skip_hidden_sheets: bool = True
    pivot_to_long_form: bool = True
    collapse_repeated_runs: bool = True
    promote_glossary: bool = True
    split_multi_block_sheets: bool = True
    normalize_whitespace: bool = True
    relative_paths: bool = True
    pdf_extract_tables: bool = True
    ai_summary: bool = True


class CleanResult(BaseModel):
    """Output of /api/clean."""
    document_id: str
    cleaned_markdown: str
    cleaning_log: list[CleaningStep] = Field(default_factory=list)
    summary: str | None = None
    summary_blocked_reason: str | None = None
    raw_size: int
    cleaned_size: int
    clean_ai_cost: "AICostEstimate | None" = None


class CleanSaveRequest(BaseModel):
    """Body for /api/clean/save."""
    document_id: str
    cleaned_markdown: str
    cleaning_log: list[CleaningStep] = Field(default_factory=list)
    summary: str | None = None


# ---------------------------------------------------------------------------
# AI Cost Estimation
# ---------------------------------------------------------------------------

class AICostCall(BaseModel):
    """Token usage and cost for one AI API call."""
    call_type: str  # "image_analysis" | "document_plugin"
    location: str   # e.g. "Sheet1/B5" or "MarkItDown LLM call 1"
    model: str
    input_tokens: int
    output_tokens: int
    total_cost_usd: float


class AICostEstimate(BaseModel):
    """Aggregated AI cost for a single conversion."""
    model: str
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    cost_per_call: list[AICostCall] = Field(default_factory=list)
    pricing_source: str = "Azure OpenAI pay-as-you-go Global Standard (USD)"
    summary: str | None = None


class CleanSaveResponse(BaseModel):
    """Result of saving the cleaned markdown as canonical."""
    document_id: str
    canonical_path: str
    raw_backup_path: str
    manifest_path: str


# ---------------------------------------------------------------------------
# Knowledge Hub chat (RAG)
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    """One turn of conversation history sent by the client."""
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    """Body for POST /api/chat."""
    question: str
    history: list[ChatMessage] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=12)
    mode: str = Field(
        default="graph",
        description=(
            '"core" for BM25-only retrieval, "graph" to expand those hits over the '
            "knowledge graph before re-ranking. Falls back to core when no graph "
            "snapshot has been compiled."
        ),
    )


class ChatSource(BaseModel):
    """One retrieved knowledge-base passage behind an answer."""
    index: int = Field(description="1-based citation number shown to the agent")
    chunk_id: str
    document_id: str
    document_title: str
    heading: str = ""
    excerpt: str
    score: float = Field(description="Ranking score: BM25 in core mode, the fused score in graph mode")
    cited: bool = Field(default=False, description="True when the answer drew on this passage")
    # Present only in graph mode, so a passage in the answer can always be
    # traced to the reason it was retrieved.
    origin: str | None = Field(
        default=None, description='"seed" when BM25 found it, "expanded" when the graph walk did'
    )
    retrieval_reason: str | None = Field(
        default=None, description="Plain-language account of how this passage was reached"
    )
    lexical_score: float | None = Field(default=None, description="Normalised BM25 contribution")
    graph_score: float | None = Field(default=None, description="Normalised graph-expansion contribution")
    hops: int | None = Field(default=None, description="Graph steps from the nearest seed passage")


class RetrievalReport(BaseModel):
    """What retrieval actually did, so an answer can be audited after the fact."""
    mode: str = Field(description='"core" or "graph" — the mode that ran, not the one requested')
    requested_mode: str
    fell_back: bool = Field(default=False, description="True when graph mode was asked for and could not run")
    fallback_reason: str | None = None
    seed_count: int = 0
    expanded_count: int = Field(default=0, description="Passages in the answer that only the graph found")
    linked_concepts: list[str] = Field(default_factory=list)
    nodes_reached: int = 0
    edges_traversed: int = 0
    #: Node and edge ids the graph view highlights when replaying this answer.
    highlight_nodes: list[str] = Field(default_factory=list)
    highlight_edges: list[dict[str, Any]] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    graph_stale: bool = Field(
        default=False,
        description="True when the graph snapshot was compiled from a different corpus than the one indexed",
    )


class ChatResponse(BaseModel):
    """Structured, call-ready answer (PRD 3.2 / 3.5 / 3.8)."""
    answered: bool
    direct_answer: str
    key_details: list[str] = Field(default_factory=list)
    important_notes: list[str] = Field(default_factory=list)
    confidence: str = "Low"  # "High" | "Medium" | "Low"
    sources: list[ChatSource] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)
    suggested_topics: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    latency_ms: int = 0
    retrieval_ms: int = Field(default=0, description="Time in retrieval alone, excluding generation")
    model: str | None = None
    ai_cost: "AICostEstimate | None" = None
    retrieval: "RetrievalReport | None" = None


class KnowledgeDocument(BaseModel):
    """A document available to the chatbot."""
    document_id: str
    document_title: str
    source_filename: str
    source_type: str
    classification: str = "Internal"
    department: str | None = None
    access_group: str | None = None
    version: str | None = None
    converted_at_utc: str | None = None
    char_count: int = 0
    chunk_count: int = 0


class KnowledgeStats(BaseModel):
    """Size and readiness of the knowledge base."""
    document_count: int
    chunk_count: int
    model: str
    model_configured: bool


class KnowledgeOverview(BaseModel):
    """Everything the chat page needs on first load."""
    stats: KnowledgeStats
    documents: list[KnowledgeDocument] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    """Body for POST /api/chat/feedback (PRD 3.7)."""
    question: str
    answer: str
    rating: str = Field(description='"up" or "down"')
    comment: str | None = None
    confidence: str | None = None
    document_ids: list[str] = Field(default_factory=list)


class FeedbackResponse(BaseModel):
    recorded: bool
    log_path: str