import {
  AlertTriangle,
  Check,
  Copy,
  DollarSign,
  Download,
  FileText,
  Info,
  Library,
  LoaderCircle,
  Network,
  RotateCcw,
  Save,
  Shield,
  Sparkles,
  TrendingUp,
  UploadCloud,
  Wand2,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useState, type DragEvent, type FormEvent } from "react";

import {
  API_BASE_URL,
  fetchKnowledgeOverview,
  getJson,
  postForm,
  postJson,
  type CapabilitiesResponse,
  type CleanOptions,
  type CleanResult,
  type ConversionResponse,
  type FileAnalysis,
  type KnowledgeDocument,
  type PricingInfo,
} from "../api";
import { knowledgeGraphUrl } from "./KnowledgeGraphView";

const defaultCleanOptions: CleanOptions = {
  skip_hidden_sheets: true,
  pivot_to_long_form: true,
  collapse_repeated_runs: true,
  promote_glossary: true,
  split_multi_block_sheets: true,
  normalize_whitespace: true,
  relative_paths: true,
  pdf_extract_tables: true,
  ai_summary: true,
};

const cleanOptionLabels: { id: keyof CleanOptions; label: string; help: string; usesAI: boolean }[] = [
  { id: "skip_hidden_sheets", label: "Skip hidden / admin sheets", help: "Drops sheets with sheet_state != visible or admin-style names", usesAI: false },
  { id: "pivot_to_long_form", label: "Reshape pivot tables (Plan × Code) into long form", help: "One fact per row — far easier for AI retrieval (deterministic, no AI cost)", usesAI: false },
  { id: "collapse_repeated_runs", label: "Collapse repeated-value runs", help: "e.g. 'Full cover (×41 columns)' instead of 41 identical cells (deterministic)", usesAI: false },
  { id: "promote_glossary", label: "Promote definitions to a top-level Glossary", help: "Pulls 'Information' / 'Definitions' sheets to the top of the doc (deterministic)", usesAI: false },
  { id: "split_multi_block_sheets", label: "Split multi-block sheets", help: "Break sheets that contain several tables separated by blank rows (deterministic)", usesAI: false },
  { id: "pdf_extract_tables", label: "Re-extract PDF tables (pdfplumber)", help: "Recovers procedure↔refund pairings lost in plain text PDF output (deterministic)", usesAI: false },
  { id: "relative_paths", label: "Rewrite absolute paths to repo-relative", help: "Removes user/host info from frontmatter (deterministic)", usesAI: false },
  { id: "normalize_whitespace", label: "Normalize whitespace", help: "Trim trailing spaces and collapse blank lines (deterministic)", usesAI: false },
  { id: "ai_summary", label: "AI-generated 3-sentence summary", help: "Calls Azure OpenAI — costs tokens. Blocked for Confidential / Restricted.", usesAI: true },
];

type MetadataForm = {
  documentTitle: string;
  department: string;
  accessGroup: string;
  classification: string;
  documentOwner: string;
  additionalContext: string;
  version: string;
  expiryReviewDate: string;
};

const emptyForm: MetadataForm = {
  documentTitle: "",
  department: "Claims",
  accessGroup: "KH_CLAIMS_USERS",
  classification: "Internal",
  documentOwner: "",
  additionalContext: "",
  version: "",
  expiryReviewDate: "",
};

/** AI is never allowed to see these classifications. */
function isAiBlockedClassification(classification: string): boolean {
  return classification === "Confidential" || classification === "Restricted";
}

type DocumentsPageProps = {
  knowledgeVersion: number;
  onKnowledgeChanged: () => void;
  /**
   * Open the knowledge graph.
   *
   * The graph belongs to this page: it draws what these documents became once
   * indexed. Opening it in place rather than in a new tab keeps the two a
   * single back-and-forth.
   */
  onOpenGraph?: () => void;
};

function DocumentsPage({ knowledgeVersion, onKnowledgeChanged, onOpenGraph }: DocumentsPageProps) {
  const [file, setFile] = useState<File | null>(null);
  const [metadata, setMetadata] = useState<MetadataForm>(emptyForm);
  const [result, setResult] = useState<ConversionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isConverting, setIsConverting] = useState(false);
  const [hasCopied, setHasCopied] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [enablePlugin, setEnablePlugin] = useState(false);
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null);
  const [showCapabilities, setShowCapabilities] = useState(false);
  const [fileAnalysis, setFileAnalysis] = useState<FileAnalysis | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [activeTab, setActiveTab] = useState<"output" | "clean">("output");
  const [cleanOptions, setCleanOptions] = useState<CleanOptions>(defaultCleanOptions);
  const [cleanResult, setCleanResult] = useState<CleanResult | null>(null);
  const [isCleaning, setIsCleaning] = useState(false);
  const [cleanError, setCleanError] = useState<string | null>(null);
  const [cleanCopied, setCleanCopied] = useState(false);
  const [isSavingClean, setIsSavingClean] = useState(false);
  const [cleanSavedAt, setCleanSavedAt] = useState<string | null>(null);
  const [pricingInfo, setPricingInfo] = useState<PricingInfo | null>(null);
  const [library, setLibrary] = useState<KnowledgeDocument[] | null>(null);

  useEffect(() => {
    getJson<CapabilitiesResponse>("/api/capabilities").then(setCapabilities).catch(() => {});
    getJson<PricingInfo>("/api/pricing").then(setPricingInfo).catch(() => {});
  }, []);

  useEffect(() => {
    fetchKnowledgeOverview()
      .then((overview) => setLibrary(overview.documents))
      .catch(() => setLibrary(null));
  }, [knowledgeVersion]);

  // Live cost estimate shown in the upload form
  const liveCostEstimate = useMemo(() => {
    if (!file) return null;
    const fileSizeMb = file.size / (1024 * 1024);
    const modelName = pricingInfo?.deployment ?? "gpt-4.1";
    if (!enablePlugin) {
      return {
        label: "No AI cost",
        detail: "Standard conversion only — no Azure OpenAI calls are made.",
        usd: 0,
        breakdown: [] as { label: string; usd: number }[],
        isEstimate: false,
      };
    }
    const imageCount = fileAnalysis?.image_count ?? 0;
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    const supportsPlugin = ["pdf", "docx", "pptx"].includes(ext);
    if (!supportsPlugin) {
      return {
        label: "No AI cost",
        detail: `AI plugin is enabled but .${ext} files use standard conversion only.`,
        usd: 0,
        breakdown: [] as { label: string; usd: number }[],
        isEstimate: false,
      };
    }
    const INPUT_PER_1M = pricingInfo?.input_per_1m_tokens_usd ?? 2.0;
    const OUTPUT_PER_1M = pricingInfo?.output_per_1m_tokens_usd ?? 8.0;
    const tokensIn = 1500;
    const tokensOut = 600;
    const costPerImage = (tokensIn / 1_000_000) * INPUT_PER_1M + (tokensOut / 1_000_000) * OUTPUT_PER_1M;
    const pagesEst = Math.max(1, Math.round(fileSizeMb * 10));
    const pluginTokensIn = pagesEst * 400;
    const pluginTokensOut = pagesEst * 200;
    const pluginCost =
      (pluginTokensIn / 1_000_000) * INPUT_PER_1M + (pluginTokensOut / 1_000_000) * OUTPUT_PER_1M;
    const imageCost = costPerImage * imageCount;
    const total = pluginCost + imageCost;
    const breakdown: { label: string; usd: number }[] = [
      { label: `Document text pass (~${pagesEst} page est.)`, usd: pluginCost },
    ];
    if (imageCount > 0) {
      breakdown.push({ label: `${imageCount} image analysis (OCR + description)`, usd: imageCost });
    }
    return {
      label: `~$${total.toFixed(5)} USD`,
      detail: `${modelName} · pay-as-you-go · ${imageCount > 0 ? imageCount + " image(s) + " : ""}document pass`,
      usd: total,
      breakdown,
      isEstimate: true,
    };
  }, [file, enablePlugin, fileAnalysis, pricingInfo]);

  // Pre-run cost estimate for the Clean Data AI summary step
  const cleanCostEstimate = useMemo(() => {
    if (!result) return null;
    const aiBlocked = isAiBlockedClassification(metadata.classification);
    if (!cleanOptions.ai_summary || aiBlocked) {
      return {
        usd: 0,
        label: "$0.00",
        detail: aiBlocked
          ? "AI summary blocked for this classification."
          : "AI summary is disabled — no AI cost.",
      };
    }
    const modelName = pricingInfo?.deployment ?? "gpt-4.1";
    const INPUT_PER_1M = pricingInfo?.input_per_1m_tokens_usd ?? 2.0;
    const OUTPUT_PER_1M = pricingInfo?.output_per_1m_tokens_usd ?? 8.0;
    const inp = 2300;
    const out = 240;
    const usd = (inp / 1_000_000) * INPUT_PER_1M + (out / 1_000_000) * OUTPUT_PER_1M;
    return {
      usd,
      label: `~$${usd.toFixed(5)}`,
      detail: `${modelName} · 1 call · ~${inp} input + ${out} output tokens`,
    };
  }, [result, cleanOptions.ai_summary, metadata.classification, pricingInfo]);

  const downloadUrl = useMemo(() => {
    if (!result?.output_file) return null;
    const filename = result.output_file.split(/[\\/]/).pop();
    return filename ? `${API_BASE_URL}/api/outputs/${encodeURIComponent(filename)}` : null;
  }, [result]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("Select a document before converting.");
      return;
    }

    const payload = new FormData();
    payload.append("file", file);
    payload.append("document_title", metadata.documentTitle);
    payload.append("department", metadata.department);
    payload.append("access_group", metadata.accessGroup);
    payload.append("classification", metadata.classification);
    payload.append("document_owner", metadata.documentOwner);
    payload.append("additional_context", metadata.additionalContext);
    payload.append("version", metadata.version);
    payload.append("expiry_review_date", metadata.expiryReviewDate);
    payload.append("enable_plugin", enablePlugin ? "true" : "false");

    setIsConverting(true);
    setError(null);
    try {
      const body = await postForm<ConversionResponse>("/api/convert", payload);
      setResult(body);
      setHasCopied(false);
      setCleanResult(null);
      setCleanError(null);
      setCleanSavedAt(null);
      setActiveTab("output");
      onKnowledgeChanged(); // the chatbot can search it now
    } catch (caughtError) {
      setResult(null);
      setError(caughtError instanceof Error ? caughtError.message : "Conversion failed.");
    } finally {
      setIsConverting(false);
    }
  }

  function handleSelectedFile(selectedFile: File | null) {
    setFile(selectedFile);
    setResult(null);
    setError(null);
    setHasCopied(false);
    setFileAnalysis(null);
    if (selectedFile) {
      void analyzeFile(selectedFile);
    }
  }

  async function analyzeFile(selectedFile: File) {
    setIsAnalyzing(true);
    try {
      const payload = new FormData();
      payload.append("file", selectedFile);
      payload.append("classification", metadata.classification);
      const analysis = await postForm<FileAnalysis>("/api/analyze", payload);
      setFileAnalysis(analysis);
      if (analysis.ai_blocked) {
        setEnablePlugin(false);
      } else if (analysis.recommendation === "ai_recommended" || analysis.recommendation === "ai_required") {
        setEnablePlugin(true);
      }
    } catch {
      // Analysis is optional — never block the user on it.
    } finally {
      setIsAnalyzing(false);
    }
  }

  function handleDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setIsDragging(false);
    handleSelectedFile(event.dataTransfer.files.item(0));
  }

  async function handleCopyMarkdown() {
    if (!result?.markdown) return;
    await navigator.clipboard.writeText(result.markdown);
    setHasCopied(true);
    window.setTimeout(() => setHasCopied(false), 1800);
  }

  function handleReset() {
    setFile(null);
    setResult(null);
    setError(null);
    setHasCopied(false);
    setMetadata(emptyForm);
    setFileAnalysis(null);
    setEnablePlugin(false);
    setCleanResult(null);
    setCleanError(null);
    setCleanSavedAt(null);
    setCleanOptions(defaultCleanOptions);
    setActiveTab("output");
  }

  function toggleCleanOption(key: keyof CleanOptions) {
    setCleanOptions((previous) => ({ ...previous, [key]: !previous[key] }));
  }

  async function runClean() {
    if (!result) return;
    setIsCleaning(true);
    setCleanError(null);
    setCleanSavedAt(null);
    try {
      const payload = new FormData();
      payload.append("document_id", result.metadata.document_id);
      payload.append("classification", metadata.classification);
      payload.append("options", JSON.stringify(cleanOptions));
      setCleanResult(await postForm<CleanResult>("/api/clean", payload));
    } catch (caught) {
      setCleanResult(null);
      setCleanError(caught instanceof Error ? caught.message : "Cleaning failed.");
    } finally {
      setIsCleaning(false);
    }
  }

  async function saveClean() {
    if (!cleanResult || !result) return;
    setIsSavingClean(true);
    setCleanError(null);
    try {
      await postJson("/api/clean/save", {
        document_id: cleanResult.document_id,
        cleaned_markdown: cleanResult.cleaned_markdown,
        cleaning_log: cleanResult.cleaning_log,
        summary: cleanResult.summary,
      });
      setCleanSavedAt(new Date().toLocaleTimeString());
      setResult({ ...result, markdown: cleanResult.cleaned_markdown });
      onKnowledgeChanged(); // the chatbot now searches the cleaned text
    } catch (caught) {
      setCleanError(caught instanceof Error ? caught.message : "Save failed.");
    } finally {
      setIsSavingClean(false);
    }
  }

  async function copyCleaned() {
    if (!cleanResult?.cleaned_markdown) return;
    await navigator.clipboard.writeText(cleanResult.cleaned_markdown);
    setCleanCopied(true);
    window.setTimeout(() => setCleanCopied(false), 1800);
  }

  function getScoreColor(score: number): string {
    if (score >= 85) return "#0f7b3f";
    if (score >= 65) return "#a66d08";
    return "#b42318";
  }

  function getScoreLabel(score: number): string {
    if (score >= 85) return "High Confidence";
    if (score >= 65) return "Medium Confidence";
    return "Low Confidence";
  }

  const aiBlocked = isAiBlockedClassification(metadata.classification);

  return (
    <div className="documents-page">
      <section className="page-head">
        <div>
          <p className="eyebrow">Documents</p>
          <h1>Convert &amp; publish to the knowledge base</h1>
          <p className="page-sub">
            Turn a source file into clean Markdown, then it is searchable by the Knowledge Assistant.
          </p>
        </div>
        <div className="page-head-actions">
          {/* The structure these documents become once indexed: sections,
              chunks, the terms mined from them, and the relations retrieval
              walks. This is the only way into it, which is why it is stated
              here rather than left as an icon. */}
          {onOpenGraph ? (
            <button className="btn btn-graph" type="button" onClick={onOpenGraph}>
              <Network size={17} />
              Explore knowledge graph
            </button>
          ) : (
            <a className="btn btn-graph" href={knowledgeGraphUrl()} target="_blank" rel="noreferrer">
              <Network size={17} />
              Explore knowledge graph
            </a>
          )}
          <button
            className="btn btn-quiet"
            onClick={() => setShowCapabilities(!showCapabilities)}
            type="button"
          >
            <Info size={17} />
            {showCapabilities ? "Hide" : "Show"} capabilities &amp; limitations
          </button>
        </div>
      </section>

      {showCapabilities && capabilities && (
        <section className="card capabilities-panel">
          <div className="capabilities-grid">
            <div>
              <h3>
                <Shield size={16} /> What we can do
              </h3>
              <ul className="capability-list supported">
                {capabilities.supported.map((item) => (
                  <li key={item.feature}>
                    <strong>{item.feature}</strong>
                    {item.plugin_required && <span className="plugin-badge">Plugin</span>}
                    <br />
                    <span className="cap-detail">{item.details}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3>
                <AlertTriangle size={16} /> Known limitations
              </h3>
              <ul className="capability-list limitations">
                {capabilities.limitations.map((item) => (
                  <li key={item.feature}>
                    <strong>{item.feature}</strong>
                    {item.plugin_required && <span className="plugin-badge">Needs plugin</span>}
                    <br />
                    <span className="cap-detail">{item.details}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="capabilities-footer">
            <span>Supported formats: {capabilities.file_types.join(", ")}</span>
            <span>Max file size: {capabilities.max_file_size_mb} MB</span>
            <span>AI plugin: {capabilities.plugin_available ? "Configured" : "Not configured"}</span>
          </div>
        </section>
      )}

      {/* Everything the assistant can currently search. */}
      <section className="card library-panel">
        <div className="library-head">
          <h3>
            <Library size={16} /> Indexed documents
          </h3>
          <span className="library-count">
            {library ? `${library.length} in the knowledge base` : "loading…"}
          </span>
        </div>
        {library && library.length > 0 ? (
          <div className="library-table-wrap">
            <table className="library-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Source</th>
                  <th>Type</th>
                  <th>Classification</th>
                  <th>Department</th>
                  <th>Passages</th>
                </tr>
              </thead>
              <tbody>
                {library.map((document) => (
                  <tr key={document.document_id}>
                    <td>
                      <strong>{document.document_title}</strong>
                    </td>
                    <td className="cell-muted">{document.source_filename}</td>
                    <td>
                      <span className="type-chip">{document.source_type}</span>
                    </td>
                    <td>
                      <span
                        className={`class-chip${
                          isAiBlockedClassification(document.classification) ? " is-restricted" : ""
                        }`}
                      >
                        {document.classification}
                      </span>
                    </td>
                    <td className="cell-muted">{document.department ?? "—"}</td>
                    <td>{document.chunk_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="library-empty">
            Nothing indexed yet. Convert a document below and it appears here.
          </p>
        )}
      </section>

      <section className="workbench-grid">
        <form className="card control-panel" onSubmit={handleSubmit}>
          <label
            className={`drop-zone${isDragging ? " is-dragging" : ""}`}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
          >
            <UploadCloud size={28} strokeWidth={1.8} />
            <span>
              {file
                ? file.name
                : "Drag and drop a file here, or select PDF, Excel, Word, CSV or another source file"}
            </span>
            <input
              type="file"
              accept=".csv,.doc,.docx,.epub,.html,.htm,.json,.md,.msg,.pdf,.pptx,.rtf,.txt,.xls,.xlsm,.xlsx,.xml,.zip"
              onChange={(event) => handleSelectedFile(event.target.files?.[0] ?? null)}
            />
          </label>

          {isAnalyzing && (
            <div className="analysis-banner analyzing">
              <LoaderCircle className="spin" size={16} />
              <span>Analyzing file for best conversion mode…</span>
            </div>
          )}
          {fileAnalysis && !isAnalyzing && (
            <div className={`analysis-banner ${fileAnalysis.recommendation}`}>
              {fileAnalysis.recommendation === "ai_recommended" && <Zap size={16} />}
              {fileAnalysis.recommendation === "ai_required" && <AlertTriangle size={16} />}
              {fileAnalysis.recommendation === "standard" && <Check size={16} />}
              <div className="analysis-content">
                <span className="analysis-title">
                  {fileAnalysis.recommendation === "ai_recommended" && "AI plugin recommended"}
                  {fileAnalysis.recommendation === "ai_required" && "AI plugin required"}
                  {fileAnalysis.recommendation === "standard" && "Standard conversion sufficient"}
                </span>
                <span className="analysis-detail">{fileAnalysis.recommendation_reason}</span>
                {fileAnalysis.has_images && (
                  <span className="analysis-stats">
                    {fileAnalysis.image_count > 0
                      ? `${fileAnalysis.image_count} image(s) detected`
                      : "Images detected"}
                    {fileAnalysis.has_merged_cells ? " · Merged cells found" : ""}
                    {fileAnalysis.sheet_count ? ` · ${fileAnalysis.sheet_count} sheet(s)` : ""}
                  </span>
                )}
                {fileAnalysis.ai_blocked && (
                  <span className="analysis-blocked">
                    <Shield size={13} /> {fileAnalysis.ai_blocked_reason}
                  </span>
                )}
                {!fileAnalysis.ai_blocked && fileAnalysis.estimated_ai_cost_note && (
                  <span className="analysis-cost-estimate">
                    <DollarSign size={13} /> {fileAnalysis.estimated_ai_cost_note}
                  </span>
                )}
              </div>
            </div>
          )}

          <div className={`plugin-toggle-row${fileAnalysis?.ai_blocked ? " disabled" : ""}`}>
            <label className="toggle-label">
              <input
                type="checkbox"
                checked={enablePlugin}
                onChange={(event) => setEnablePlugin(event.target.checked)}
                disabled={fileAnalysis?.ai_blocked || false}
              />
              <Zap size={16} />
              <span>Enable AI plugin (Azure OpenAI)</span>
            </label>
            <span className="toggle-hint">
              {fileAnalysis?.ai_blocked
                ? `AI disabled for ${metadata.classification} documents — data will not be sent to external AI.`
                : enablePlugin
                  ? "Images will be described via GPT (OCR + description). May increase conversion time."
                  : "Standard conversion only. Images/scanned PDFs will be skipped."}
            </span>
          </div>

          <div className="field-grid">
            <label>
              Document title
              <input
                value={metadata.documentTitle}
                placeholder={file?.name.replace(/\.[^.]+$/, "") ?? ""}
                onChange={(event) => setMetadata({ ...metadata, documentTitle: event.target.value })}
              />
            </label>
            <label>
              Department
              <input
                value={metadata.department}
                onChange={(event) => setMetadata({ ...metadata, department: event.target.value })}
              />
            </label>
            <label>
              Access group
              <input
                value={metadata.accessGroup}
                onChange={(event) => setMetadata({ ...metadata, accessGroup: event.target.value })}
              />
            </label>
            <label>
              Classification
              <select
                value={metadata.classification}
                onChange={(event) => {
                  const nextClassification = event.target.value;
                  setMetadata({ ...metadata, classification: nextClassification });
                  if (isAiBlockedClassification(nextClassification)) {
                    setEnablePlugin(false);
                    setFileAnalysis((previous) =>
                      previous
                        ? {
                            ...previous,
                            ai_blocked: true,
                            ai_blocked_reason: `AI plugin is disabled for '${nextClassification}' documents. Sensitive data must not be sent to external AI services.`,
                          }
                        : previous,
                    );
                  } else {
                    setFileAnalysis((previous) =>
                      previous ? { ...previous, ai_blocked: false, ai_blocked_reason: null } : previous,
                    );
                  }
                }}
              >
                <option>General</option>
                <option>Internal</option>
                <option>Confidential</option>
                <option>Restricted</option>
              </select>
            </label>
            <label>
              Owner
              <input
                value={metadata.documentOwner}
                onChange={(event) => setMetadata({ ...metadata, documentOwner: event.target.value })}
              />
            </label>
            <label>
              Other category / description
              <textarea
                value={metadata.additionalContext}
                placeholder="Add missing category, business meaning, sheet notes, known column context, or anything the AI should know before reading this file."
                onChange={(event) =>
                  setMetadata({ ...metadata, additionalContext: event.target.value })
                }
              />
            </label>
            <label>
              Version
              <input
                value={metadata.version}
                onChange={(event) => setMetadata({ ...metadata, version: event.target.value })}
              />
            </label>
            <label>
              Review expiry
              <input
                type="date"
                value={metadata.expiryReviewDate}
                onChange={(event) =>
                  setMetadata({ ...metadata, expiryReviewDate: event.target.value })
                }
              />
            </label>
          </div>

          {file && liveCostEstimate && (
            <div className={`cost-estimate-panel${liveCostEstimate.usd === 0 ? " cost-zero" : ""}`}>
              <div className="cost-estimate-header">
                <DollarSign size={16} />
                <span>Estimated AI cost</span>
                <span className={`cost-estimate-badge${liveCostEstimate.usd === 0 ? " zero" : ""}`}>
                  {liveCostEstimate.usd === 0 ? "$0.00" : liveCostEstimate.label}
                </span>
              </div>
              <p className="cost-estimate-detail">{liveCostEstimate.detail}</p>
              {liveCostEstimate.breakdown.length > 0 && (
                <ul className="cost-estimate-breakdown">
                  {liveCostEstimate.breakdown.map((item) => (
                    <li key={item.label}>
                      <span>{item.label}</span>
                      <span>${item.usd.toFixed(5)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {liveCostEstimate.isEstimate && (
                <p className="cost-estimate-note">
                  ⚠ Estimate only. Actual cost is shown after conversion based on real token usage.
                </p>
              )}
            </div>
          )}

          {error && (
            <div className="error-banner">
              <AlertTriangle size={18} />
              <span>{error}</span>
            </div>
          )}

          <button className="btn btn-primary" disabled={isConverting} type="submit">
            {isConverting ? <LoaderCircle className="spin" size={18} /> : <FileText size={18} />}
            {isConverting ? "Converting" : "Convert to Markdown"}
          </button>
          <button
            className="btn btn-quiet"
            disabled={isConverting && !result}
            onClick={handleReset}
            type="button"
          >
            <RotateCcw size={17} />
            Reset
          </button>
        </form>

        <section className="card preview-panel">
          <div className="preview-toolbar">
            <div>
              <p className="eyebrow">Markdown output</p>
              <h2>{result?.metadata.document_title ?? "Waiting for conversion"}</h2>
              {result && (
                <div className="tab-bar" role="tablist" aria-label="Output views">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={activeTab === "output"}
                    className={`tab-button${activeTab === "output" ? " is-active" : ""}`}
                    onClick={() => setActiveTab("output")}
                  >
                    <FileText size={15} /> Output
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={activeTab === "clean"}
                    className={`tab-button${activeTab === "clean" ? " is-active" : ""}`}
                    onClick={() => setActiveTab("clean")}
                  >
                    <Wand2 size={15} /> Clean data
                    {cleanResult && <span className="tab-pill">ready</span>}
                  </button>
                </div>
              )}
            </div>
            <div className="preview-actions">
              {result && activeTab === "output" && (
                <button className="btn btn-quiet btn-sm" onClick={handleCopyMarkdown} type="button">
                  {hasCopied ? <Check size={16} /> : <Copy size={16} />}
                  {hasCopied ? "Copied" : "Copy"}
                </button>
              )}
              {result && activeTab === "clean" && cleanResult && (
                <button className="btn btn-quiet btn-sm" onClick={copyCleaned} type="button">
                  {cleanCopied ? <Check size={16} /> : <Copy size={16} />}
                  {cleanCopied ? "Copied" : "Copy"}
                </button>
              )}
              {downloadUrl && activeTab === "output" && (
                <a className="btn btn-accent btn-sm" href={downloadUrl}>
                  <Download size={16} />
                  Download
                </a>
              )}
            </div>
          </div>

          {result && activeTab === "output" ? (
            <>
              <div className="confidence-card">
                <div className="confidence-overall">
                  <div
                    className="confidence-circle"
                    style={{ borderColor: getScoreColor(result.confidence.overall) }}
                  >
                    <span
                      className="confidence-number"
                      style={{ color: getScoreColor(result.confidence.overall) }}
                    >
                      {Math.round(result.confidence.overall)}
                    </span>
                    <span className="confidence-unit">/100</span>
                  </div>
                  <div>
                    <p
                      className="confidence-label"
                      style={{ color: getScoreColor(result.confidence.overall) }}
                    >
                      {getScoreLabel(result.confidence.overall)}
                    </p>
                    {result.plugin_used && (
                      <p className="plugin-indicator">
                        <Zap size={13} /> {result.plugin_name}
                      </p>
                    )}
                  </div>
                </div>
                <div className="confidence-breakdown">
                  {(
                    [
                      ["Structure", result.confidence.structure_fidelity],
                      ["Content", result.confidence.content_completeness],
                      ["Images", result.confidence.image_handling],
                      ["Formatting", result.confidence.formatting_preservation],
                    ] as const
                  ).map(([label, score]) => (
                    <div className="score-bar" key={label}>
                      <span>{label}</span>
                      <div className="bar-track">
                        <div
                          className="bar-fill"
                          style={{ width: `${score}%`, background: getScoreColor(score) }}
                        />
                      </div>
                      <span className="bar-value">{Math.round(score)}</span>
                    </div>
                  ))}
                </div>
              </div>

              {result.ai_cost && (
                <div className="cost-card">
                  <div className="cost-card-header">
                    <DollarSign size={16} />
                    <span>AI cost — this conversion</span>
                    <span className="cost-badge">${result.ai_cost.total_cost_usd.toFixed(5)}</span>
                  </div>
                  <div className="cost-token-row">
                    <TrendingUp size={14} />
                    <span>
                      <strong>{result.ai_cost.total_input_tokens.toLocaleString()}</strong> input&nbsp;+&nbsp;
                      <strong>{result.ai_cost.total_output_tokens.toLocaleString()}</strong> output
                      tokens&nbsp;·&nbsp;Model: <em>{result.ai_cost.model}</em>
                    </span>
                  </div>
                  {result.ai_cost.cost_per_call.length > 0 && (
                    <details className="cost-breakdown">
                      <summary>
                        Per-call breakdown ({result.ai_cost.cost_per_call.length} call
                        {result.ai_cost.cost_per_call.length !== 1 ? "s" : ""})
                      </summary>
                      <table className="cost-table">
                        <thead>
                          <tr>
                            <th>Location / call</th>
                            <th>Type</th>
                            <th>In</th>
                            <th>Out</th>
                            <th>Cost (USD)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.ai_cost.cost_per_call.map((call, index) => (
                            <tr key={`${call.location}-${index}`}>
                              <td>{call.location}</td>
                              <td>{call.call_type === "image_analysis" ? "Image analysis" : "Doc plugin"}</td>
                              <td>{call.input_tokens.toLocaleString()}</td>
                              <td>{call.output_tokens.toLocaleString()}</td>
                              <td>${call.total_cost_usd.toFixed(6)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </details>
                  )}
                  <p className="cost-note">{result.ai_cost.pricing_source}</p>
                </div>
              )}

              {(result.confidence.factors.length > 0 || result.confidence.limitations_hit.length > 0) && (
                <div className="confidence-details">
                  {result.confidence.factors.length > 0 && (
                    <div className="factors-section">
                      <h4>Scoring factors</h4>
                      <ul>
                        {result.confidence.factors.map((factor) => (
                          <li key={factor}>{factor}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {result.confidence.limitations_hit.length > 0 && (
                    <div className="limitations-section">
                      <h4>Limitations encountered</h4>
                      <ul>
                        {result.confidence.limitations_hit.map((limitation) => (
                          <li key={limitation}>{limitation}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              <div className="output-meta">
                <span>{result.metadata.source_type}</span>
                <span>{result.metadata.extraction_strategy}</span>
                <span>
                  {result.metadata.sheet_count ? `${result.metadata.sheet_count} sheets` : "document"}
                </span>
              </div>

              {result.warnings.length > 0 && (
                <div className="warning-list">
                  {result.warnings.map((warning) => (
                    <p key={warning}>{warning}</p>
                  ))}
                </div>
              )}

              <pre className="markdown-preview">{result.markdown}</pre>

              <div className="saved-paths">
                <p>Markdown: {result.output_file}</p>
                <p>Manifest: {result.manifest_file}</p>
              </div>
            </>
          ) : result && activeTab === "clean" ? (
            <div className="clean-panel">
              <div className="clean-intro">
                <p>
                  The Clean Data view applies AI-readability transforms to the converted Markdown so a
                  knowledge base can use it directly. Toggle individual steps and click{" "}
                  <strong>Run cleaner</strong>. The original Markdown on the Output tab is not changed
                  unless you click <strong>Save as canonical</strong>.
                </p>
              </div>

              <div className="clean-options">
                {cleanOptionLabels.map((option) => {
                  const disabled = option.id === "ai_summary" && aiBlocked;
                  return (
                    <label
                      key={option.id}
                      className={`clean-option${disabled ? " disabled" : ""}${option.usesAI ? " uses-ai" : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={!disabled && cleanOptions[option.id]}
                        disabled={disabled}
                        onChange={() => toggleCleanOption(option.id)}
                      />
                      <span className="clean-option-label">
                        {option.label}
                        {option.usesAI ? (
                          <span className="ai-badge">AI · costs tokens</span>
                        ) : (
                          <span className="standard-badge">Standard</span>
                        )}
                      </span>
                      <span className="clean-option-help">
                        {disabled
                          ? `Blocked: ${metadata.classification} classification cannot be sent to external AI.`
                          : option.help}
                      </span>
                    </label>
                  );
                })}
              </div>

              {cleanCostEstimate && (
                <div className={`cost-estimate-panel${cleanCostEstimate.usd === 0 ? " cost-zero" : ""}`}>
                  <div className="cost-estimate-header">
                    <DollarSign size={16} />
                    <span>Estimated AI cost — run cleaner</span>
                    <span className={`cost-estimate-badge${cleanCostEstimate.usd === 0 ? " zero" : ""}`}>
                      {cleanCostEstimate.label}
                    </span>
                  </div>
                  <p className="cost-estimate-detail">{cleanCostEstimate.detail}</p>
                  {cleanCostEstimate.usd > 0 && (
                    <p className="cost-estimate-note">
                      ⚠ Estimate only. Actual cost is shown after running based on real token usage.
                    </p>
                  )}
                </div>
              )}

              <div className="clean-actions">
                <button type="button" className="btn btn-primary" onClick={runClean} disabled={isCleaning}>
                  {isCleaning ? <LoaderCircle className="spin" size={17} /> : <Wand2 size={17} />}
                  {isCleaning ? "Cleaning" : cleanResult ? "Re-run cleaner" : "Run cleaner"}
                </button>
                {cleanResult && (
                  <button
                    type="button"
                    className="btn btn-quiet"
                    onClick={saveClean}
                    disabled={isSavingClean}
                  >
                    {isSavingClean ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}
                    Save as canonical
                  </button>
                )}
              </div>

              {cleanError && (
                <div className="error-banner">
                  <AlertTriangle size={18} />
                  <span>{cleanError}</span>
                </div>
              )}

              {cleanSavedAt && (
                <div className="clean-saved">
                  <Check size={16} /> Cleaned Markdown saved as canonical at {cleanSavedAt}. The raw
                  output was backed up to <code>{result.metadata.document_id}.raw.md</code>.
                </div>
              )}

              {cleanResult && (
                <>
                  <div className="clean-summary-block">
                    {cleanResult.summary ? (
                      <>
                        <h4>
                          <Sparkles size={15} /> AI summary
                        </h4>
                        <p>{cleanResult.summary}</p>
                      </>
                    ) : cleanResult.summary_blocked_reason ? (
                      <>
                        <h4>
                          <Shield size={15} /> AI summary blocked
                        </h4>
                        <p className="muted">{cleanResult.summary_blocked_reason}</p>
                      </>
                    ) : null}
                  </div>

                  {cleanResult.clean_ai_cost && (
                    <div className="cost-card">
                      <div className="cost-card-header">
                        <DollarSign size={16} />
                        <span>AI cost — clean run (actual)</span>
                        <span className="cost-badge">
                          ${cleanResult.clean_ai_cost.total_cost_usd.toFixed(5)}
                        </span>
                      </div>
                      <div className="cost-token-row">
                        <TrendingUp size={14} />
                        <span>
                          <strong>{cleanResult.clean_ai_cost.total_input_tokens.toLocaleString()}</strong>{" "}
                          input&nbsp;+&nbsp;
                          <strong>{cleanResult.clean_ai_cost.total_output_tokens.toLocaleString()}</strong>{" "}
                          output tokens&nbsp;·&nbsp;Model: <em>{cleanResult.clean_ai_cost.model}</em>
                        </span>
                      </div>
                      <p className="cost-note">{cleanResult.clean_ai_cost.pricing_source}</p>
                    </div>
                  )}

                  <div className="clean-log">
                    <h4>What changed</h4>
                    <ul>
                      {cleanResult.cleaning_log.map((step) => (
                        <li key={step.id} className={step.applied ? "applied" : "skipped"}>
                          <strong>
                            {step.applied ? "✓" : "·"} {step.label}
                          </strong>
                          {step.note && <span className="step-note"> — {step.note}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="clean-size-row">
                    <span>
                      Raw: <strong>{(cleanResult.raw_size / 1024).toFixed(1)} KB</strong>
                    </span>
                    <span>
                      Cleaned: <strong>{(cleanResult.cleaned_size / 1024).toFixed(1)} KB</strong>
                    </span>
                    <span>
                      Δ:{" "}
                      <strong>
                        {((cleanResult.cleaned_size / Math.max(cleanResult.raw_size, 1)) * 100 - 100).toFixed(0)}%
                      </strong>
                    </span>
                  </div>

                  <pre className="markdown-preview">{cleanResult.cleaned_markdown}</pre>
                </>
              )}

              {!cleanResult && !isCleaning && !cleanError && (
                <div className="empty-state clean-empty">
                  <Wand2 size={36} strokeWidth={1.5} />
                  <p>Run the cleaner to see an AI-friendly version of this document.</p>
                </div>
              )}
            </div>
          ) : (
            <div className="empty-state">
              <FileText size={42} strokeWidth={1.5} />
              <p>No converted document yet.</p>
              <span className="empty-hint">
                Upload a file on the left to convert it and add it to the knowledge base.
              </span>
            </div>
          )}
        </section>
      </section>
    </div>
  );
}

export default DocumentsPage;
