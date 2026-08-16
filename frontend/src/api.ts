/**
 * Single place for the API base URL, the response types the backend returns,
 * and the thin fetch wrappers the pages call.
 */

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export type AICostCall = {
  call_type: string;
  location: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number;
};

export type AICostEstimate = {
  model: string;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  cost_per_call: AICostCall[];
  pricing_source: string;
};

export type PricingInfo = {
  deployment: string;
  input_per_1m_tokens_usd: number;
  output_per_1m_tokens_usd: number;
};

// ---------------------------------------------------------------------------
// Chat / knowledge base
// ---------------------------------------------------------------------------

export type Confidence = "High" | "Medium" | "Low";

/** Which retriever ran. `core` is BM25 alone; `graph` expands it over the graph. */
export type RetrievalMode = "core" | "graph";

export type ChatSource = {
  index: number;
  chunk_id: string;
  document_id: string;
  document_title: string;
  heading: string;
  excerpt: string;
  score: number;
  cited: boolean;
  /** Graph mode only — null in core mode rather than faked. */
  origin: "seed" | "expanded" | null;
  retrieval_reason: string | null;
  lexical_score: number | null;
  graph_score: number | null;
  hops: number | null;
};

/** What retrieval did, so an answer can be audited and replayed on the graph. */
export type RetrievalReport = {
  mode: RetrievalMode;
  requested_mode: RetrievalMode;
  fell_back: boolean;
  fallback_reason: string | null;
  seed_count: number;
  expanded_count: number;
  linked_concepts: string[];
  nodes_reached: number;
  edges_traversed: number;
  highlight_nodes: string[];
  highlight_edges: { source: string; target: string; kind: GraphRelation; hop: number }[];
  settings: Record<string, number>;
  graph_stale: boolean;
};

export type ChatResponse = {
  answered: boolean;
  direct_answer: string;
  key_details: string[];
  important_notes: string[];
  confidence: Confidence;
  sources: ChatSource[];
  follow_ups: string[];
  suggested_topics: string[];
  fallback_reason: string | null;
  latency_ms: number;
  retrieval_ms: number;
  model: string | null;
  ai_cost: AICostEstimate | null;
  retrieval: RetrievalReport | null;
};

export type KnowledgeDocument = {
  document_id: string;
  document_title: string;
  source_filename: string;
  source_type: string;
  classification: string;
  department: string | null;
  access_group: string | null;
  version: string | null;
  converted_at_utc: string | null;
  char_count: number;
  chunk_count: number;
};

export type KnowledgeStats = {
  document_count: number;
  chunk_count: number;
  model: string;
  model_configured: boolean;
};

export type KnowledgeOverview = {
  stats: KnowledgeStats;
  documents: KnowledgeDocument[];
  suggestions: string[];
};

export type HistoryTurn = { role: "user" | "assistant"; content: string };

// ---------------------------------------------------------------------------
// Converter (Documents tab)
// ---------------------------------------------------------------------------

export type ConfidenceScore = {
  overall: number;
  structure_fidelity: number;
  content_completeness: number;
  image_handling: number;
  formatting_preservation: number;
  factors: string[];
  limitations_hit: string[];
};

export type ConversionMetadata = {
  document_id: string;
  document_title: string;
  source_filename: string;
  source_type: string;
  markdown_path: string;
  extraction_strategy: string;
  department?: string | null;
  classification?: string | null;
  sheet_count?: number | null;
};

export type ConversionResponse = {
  markdown: string;
  metadata: ConversionMetadata;
  output_file: string;
  manifest_file: string;
  warnings: string[];
  confidence: ConfidenceScore;
  plugin_used: boolean;
  plugin_name: string | null;
  ai_cost: AICostEstimate | null;
};

export type CapabilityItem = {
  feature: string;
  supported: boolean;
  details: string;
  plugin_required: boolean;
};

export type CapabilitiesResponse = {
  supported: CapabilityItem[];
  limitations: CapabilityItem[];
  file_types: string[];
  max_file_size_mb: number;
  plugin_available: boolean;
};

export type FileAnalysis = {
  filename: string;
  extension: string;
  file_size_bytes: number;
  has_images: boolean;
  image_count: number;
  has_merged_cells: boolean;
  sheet_count: number | null;
  recommendation: "standard" | "ai_recommended" | "ai_required";
  recommendation_reason: string;
  ai_blocked: boolean;
  ai_blocked_reason: string | null;
  estimated_ai_cost_usd: number | null;
  estimated_ai_cost_note: string | null;
};

export type CleaningStep = {
  id: string;
  label: string;
  applied: boolean;
  note: string | null;
  metrics: Record<string, unknown>;
};

export type CleanOptions = {
  skip_hidden_sheets: boolean;
  pivot_to_long_form: boolean;
  collapse_repeated_runs: boolean;
  promote_glossary: boolean;
  split_multi_block_sheets: boolean;
  normalize_whitespace: boolean;
  relative_paths: boolean;
  pdf_extract_tables: boolean;
  ai_summary: boolean;
};

export type CleanResult = {
  document_id: string;
  cleaned_markdown: string;
  cleaning_log: CleaningStep[];
  summary: string | null;
  summary_blocked_reason: string | null;
  raw_size: number;
  cleaned_size: number;
  clean_ai_cost: AICostEstimate | null;
};

// ---------------------------------------------------------------------------
// Knowledge graph
//
// The shapes below mirror `backend/app/services/graph_builder.py` exactly. The
// backend does the graph construction, so unlike the view this replaced there
// is no second opinion about the graph on this side — the frontend draws what
// was compiled and never derives edges of its own.
// ---------------------------------------------------------------------------

export type GraphNodeType =
  | "document"
  | "section"
  | "chunk"
  | "concept"
  | "value"
  | "constraint"
  | "community"
  | "facet";

export type GraphRelation =
  | "contains"
  | "has_chunk"
  | "follows"
  | "mentions"
  | "co_occurs"
  | "in_community"
  | "similar_to"
  | "governed_by";

/**
 * One node. Every type shares `id`/`type`/`label`/`title`/`meta`; the rest are
 * per-type and optional, which is why they are all marked so rather than being
 * split into a union — the canvas only ever reads the shared five, and the
 * detail panel reads the extras behind a type check.
 */
export type KnowledgeNode = {
  id: string;
  type: GraphNodeType;
  label: string;
  title: string;
  meta: string;

  // document
  department?: string | null;
  classification?: string;
  access_group?: string | null;
  version?: string | null;
  source_filename?: string;
  source_type?: string;

  // section / chunk
  document_id?: string;
  document_title?: string;
  heading?: string;
  excerpt?: string;
  position?: number;
  chunk_count?: number;
  char_count?: number;
  token_count?: number;

  // concept / value / constraint
  normal_form?: string;
  words?: number;
  chunk_frequency?: number;
  occurrences?: number;
  specificity?: number;
  extraction?: string;
  subtype?: string;

  // community
  community_index?: number;
  concept_count?: number;
  members?: string[];

  // facet
  facet_kind?: string;
  document_count?: number;
};

export type KnowledgeEdge = {
  id: string;
  kind: GraphRelation;
  source: string;
  target: string;
  /** Relation-specific strengths, present only where the relation carries one. */
  count?: number;
  salience?: number;
  npmi?: number;
  score?: number;
  cross_document?: boolean;
  position?: number;
};

export type GraphCommunity = {
  id: string;
  index: number;
  label: string;
  concept_count: number;
  chunk_count: number;
  members: string[];
};

export type KnowledgeGraph = {
  schema_version: string;
  generated_by: string;
  generated_at_utc: string;
  corpus_fingerprint: string;
  /** Fingerprint of the corpus currently indexed — differs when the snapshot is stale. */
  indexed_corpus_fingerprint: string;
  stale: boolean;
  is_mock_corpus: boolean;
  corpus_notice: string;
  /** One line per extraction stage, so the picture can explain how it was made. */
  method: Record<string, string>;
  node_types: Record<string, { label: string; description: string }>;
  edge_types: Record<string, { label: string; inverse: string; description: string }>;
  stats: {
    documents: number;
    sections: number;
    chunks: number;
    concepts: number;
    values: number;
    constraints: number;
    communities: number;
    facets: number;
    nodes: number;
    edges: number;
    isolated_nodes: number;
    cross_document_similar_edges: number;
    mean_degree: number;
    edges_by_kind: Record<string, number>;
    nodes_by_type: Record<string, number>;
  };
  communities: GraphCommunity[];
  documents: {
    document_id: string;
    title: string;
    department: string | null;
    classification: string;
    access_group: string | null;
    source_type: string;
    chunk_count: number;
    char_count: number;
  }[];
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
};

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

/** Read a response, raising the backend's `detail` message when present. */
async function unwrap<T>(response: Response): Promise<T> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return body as T;
}

export async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return unwrap<T>(response);
}

export async function postForm<T>(path: string, payload: FormData): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "POST", body: payload });
  return unwrap<T>(response);
}

export async function getJson<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(`${API_BASE_URL}${path}`));
}

export function askQuestion(
  question: string,
  history: HistoryTurn[],
  mode: RetrievalMode = "graph",
): Promise<ChatResponse> {
  return postJson<ChatResponse>("/api/chat", { question, history, mode });
}

export function fetchKnowledgeOverview(): Promise<KnowledgeOverview> {
  return getJson<KnowledgeOverview>("/api/knowledge/overview");
}

export function fetchKnowledgeGraph(): Promise<KnowledgeGraph> {
  return getJson<KnowledgeGraph>("/api/graph/knowledge");
}

export function sendFeedback(payload: {
  question: string;
  answer: string;
  rating: "up" | "down";
  comment?: string;
  confidence?: string;
  document_ids?: string[];
}): Promise<{ recorded: boolean; log_path: string }> {
  return postJson("/api/chat/feedback", payload);
}
