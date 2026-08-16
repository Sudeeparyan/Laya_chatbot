import {
  AlertTriangle,
  ArrowUp,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  FileText,
  Info,
  Lightbulb,
  ListChecks,
  Network,
  Plus,
  Route,
  RotateCw,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Upload,
  Waypoints,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import {
  askQuestion,
  fetchKnowledgeOverview,
  sendFeedback,
  type ChatResponse,
  type ChatSource,
  type Confidence,
  type HistoryTurn,
  type KnowledgeOverview,
  type RetrievalMode,
  type RetrievalReport,
} from "../api";

type Rating = "up" | "down";

type Turn = {
  id: string;
  question: string;
  /** `null` while the answer is still in flight. */
  answer: ChatResponse | null;
  error: string | null;
  rating: Rating | null;
  comment: string;
  commentSent: boolean;
};

type Conversation = {
  id: string;
  title: string;
  turns: Turn[];
};

let idCounter = 0;
function newId(prefix: string): string {
  idCounter += 1;
  return `${prefix}-${Date.now().toString(36)}-${idCounter}`;
}

function newConversation(): Conversation {
  return { id: newId("chat"), title: "New chat", turns: [] };
}

function titleFor(question: string): string {
  const trimmed = question.trim();
  return trimmed.length > 42 ? `${trimmed.slice(0, 42)}…` : trimmed;
}

const CONFIDENCE_HINT: Record<Confidence, string> = {
  High: "The knowledge base covers this question directly.",
  Medium: "Partly covered — read the sources before quoting figures.",
  Low: "Weak match. Verify against the source document before answering the member.",
};

/**
 * What the assistant runs.
 *
 * The backend still takes a retriever per request, and still implements both —
 * BM25 alone and BM25 expanded over the knowledge graph — because comparing
 * the two on one corpus is the research question. That comparison is run from
 * the evaluation scripts, where it can be measured over a question set; it was
 * never something an agent on a call should be choosing between mid-question,
 * so the switch is not in the interface. The product answers with the graph.
 */
const RETRIEVAL_MODE: RetrievalMode = "graph";

type ChatPageProps = {
  knowledgeVersion: number;
  onGoToDocuments: () => void;
  /** Hand a retrieval trace to the graph tab so the walk can be replayed there. */
  onShowTrace?: (report: RetrievalReport, question: string) => void;
};

/** Fixed so the first conversation and the initial selection cannot disagree. */
const FIRST_CONVERSATION_ID = "chat-initial";

/**
 * What retrieval did, under the answer.
 *
 * Shown rather than hidden behind a debug flag because it is the point of the
 * comparison: an agent reading an answer should be able to see that two of its
 * six passages were found only by the graph, and go and look at why.
 */
function RetrievalSummary({
  report,
  question,
  onShowTrace,
}: {
  report: RetrievalReport;
  question: string;
  onShowTrace?: (report: RetrievalReport, question: string) => void;
}) {
  const isGraph = report.mode === "graph";

  return (
    <div className={`retrieval-summary${isGraph ? " is-graph" : ""}`}>
      <span className="retrieval-mode">
        {isGraph ? <Waypoints size={13} /> : <ListChecks size={13} />}
        {isGraph ? "Found by walking the graph" : "Found by keyword search"}
      </span>

      {report.fell_back && (
        <span className="retrieval-fallback" title={report.fallback_reason ?? undefined}>
          <AlertTriangle size={12} />
          {report.fallback_reason === "no_graph_snapshot"
            ? "no graph compiled — keyword search only"
            : "graph unavailable — keyword search only"}
        </span>
      )}

      {isGraph && (
        <>
          <span>
            <strong>{report.seed_count}</strong> seeded
          </span>
          <span>
            <strong>{report.expanded_count}</strong> added by the graph
          </span>
          <span>
            {report.nodes_reached.toLocaleString()} nodes ·{" "}
            {report.edges_traversed.toLocaleString()} edges walked
          </span>
          {report.linked_concepts.length > 0 && (
            <span className="retrieval-concepts">
              matched: {report.linked_concepts.slice(0, 3).join(", ")}
            </span>
          )}
          {report.graph_stale && (
            <span className="retrieval-fallback" title="Rebuild with build_knowledge_graph.py">
              <AlertTriangle size={12} /> graph snapshot is stale
            </span>
          )}
          {onShowTrace && report.highlight_nodes.length > 0 && (
            <button
              type="button"
              className="retrieval-trace-btn"
              onClick={() => onShowTrace(report, question)}
            >
              <Network size={12} /> Show in graph
            </button>
          )}
        </>
      )}
    </div>
  );
}

/** The "why is this here" line on a source, in graph mode only. */
function SourceProvenance({ source }: { source: ChatSource }) {
  if (!source.origin) return null;
  const expanded = source.origin === "expanded";
  return (
    <span className={`source-origin${expanded ? " is-expanded" : ""}`}>
      {expanded ? <Route size={11} /> : <Check size={11} />}
      {expanded ? `via graph, ${source.hops} hop${source.hops === 1 ? "" : "s"}` : "direct match"}
      {source.retrieval_reason && (
        <span className="source-origin-why">{source.retrieval_reason}</span>
      )}
    </span>
  );
}

function ChatPage({ knowledgeVersion, onGoToDocuments, onShowTrace }: ChatPageProps) {
  const [conversations, setConversations] = useState<Conversation[]>(() => [
    { id: FIRST_CONVERSATION_ID, title: "New chat", turns: [] },
  ]);
  const [activeId, setActiveId] = useState<string>(FIRST_CONVERSATION_ID);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [overview, setOverview] = useState<KnowledgeOverview | null>(null);
  const [copiedTurnId, setCopiedTurnId] = useState<string | null>(null);
  /**
   * Starter prompts come from the backend, so the home screen has three states
   * to tell apart and not one. Rendering the grid unconditionally turns "the
   * backend is down" and "the overview is still in flight" into the same silent
   * blank where the suggestions should be, which is indistinguishable from the
   * feature being missing.
   */
  const [overviewStatus, setOverviewStatus] = useState<"loading" | "ready" | "error">("loading");
  /** Bumped by the retry button to re-run the fetch below. */
  const [overviewAttempt, setOverviewAttempt] = useState(0);
  /** The mid-conversation strip starts open, and stays wherever the agent puts it. */
  const [suggestionsOpen, setSuggestionsOpen] = useState(true);

  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setOverviewStatus("loading");
    fetchKnowledgeOverview()
      .then((next) => {
        if (cancelled) return;
        setOverview(next);
        setOverviewStatus("ready");
      })
      .catch(() => {
        if (cancelled) return;
        setOverview(null);
        setOverviewStatus("error");
      });
    // A conversion that lands while this is in flight would otherwise be
    // overwritten by the older response.
    return () => {
      cancelled = true;
    };
  }, [knowledgeVersion, overviewAttempt]);

  const active = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId) ?? conversations[0],
    [conversations, activeId],
  );

  // Keep the newest message in view.
  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [active?.turns.length, isSending]);

  // Auto-grow the composer up to a sensible cap.
  useEffect(() => {
    const node = composerRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 180)}px`;
  }, [draft]);

  function updateTurn(conversationId: string, turnId: string, patch: Partial<Turn>) {
    setConversations((previous) =>
      previous.map((conversation) =>
        conversation.id !== conversationId
          ? conversation
          : {
              ...conversation,
              turns: conversation.turns.map((turn) =>
                turn.id === turnId ? { ...turn, ...patch } : turn,
              ),
            },
      ),
    );
  }

  async function submitQuestion(rawQuestion: string) {
    const question = rawQuestion.trim();
    if (!question || isSending || !active) return;

    const conversationId = active.id;
    const turnId = newId("turn");
    const turn: Turn = {
      id: turnId,
      question,
      answer: null,
      error: null,
      rating: null,
      comment: "",
      commentSent: false,
    };

    // Session context for follow-ups — only turns that produced an answer.
    const history: HistoryTurn[] = active.turns.flatMap((previous) =>
      previous.answer
        ? [
            { role: "user" as const, content: previous.question },
            { role: "assistant" as const, content: previous.answer.direct_answer },
          ]
        : [],
    );

    setConversations((previous) =>
      previous.map((conversation) =>
        conversation.id !== conversationId
          ? conversation
          : {
              ...conversation,
              title: conversation.turns.length === 0 ? titleFor(question) : conversation.title,
              turns: [...conversation.turns, turn],
            },
      ),
    );
    setDraft("");
    setIsSending(true);

    try {
      const answer = await askQuestion(question, history, RETRIEVAL_MODE);
      updateTurn(conversationId, turnId, { answer });
    } catch (caught) {
      updateTurn(conversationId, turnId, {
        error: caught instanceof Error ? caught.message : "The assistant could not be reached.",
      });
    } finally {
      setIsSending(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitQuestion(draft);
    }
  }

  function startNewChat() {
    const conversation = newConversation();
    setConversations((previous) => [conversation, ...previous]);
    setActiveId(conversation.id);
    setDraft("");
    composerRef.current?.focus();
  }

  function deleteConversation(conversationId: string) {
    const remaining = conversations.filter((conversation) => conversation.id !== conversationId);
    // Always leave one empty chat behind rather than an empty sidebar.
    const next = remaining.length > 0 ? remaining : [newConversation()];
    setConversations(next);
    if (conversationId === activeId) {
      setActiveId(next[0].id);
    }
  }

  async function copyAnswer(turn: Turn) {
    if (!turn.answer) return;
    const parts = [turn.answer.direct_answer];
    if (turn.answer.key_details.length > 0) {
      parts.push("", "Key details:", ...turn.answer.key_details.map((item) => `- ${item}`));
    }
    if (turn.answer.important_notes.length > 0) {
      parts.push("", "Important notes:", ...turn.answer.important_notes.map((item) => `- ${item}`));
    }
    try {
      await navigator.clipboard.writeText(parts.join("\n"));
      setCopiedTurnId(turn.id);
      window.setTimeout(() => setCopiedTurnId(null), 1800);
    } catch {
      // Clipboard permission denied — nothing useful to show the agent.
    }
  }

  function rate(turn: Turn, rating: Rating) {
    if (!turn.answer || !active) return;
    updateTurn(active.id, turn.id, { rating });
    void sendFeedback({
      question: turn.question,
      answer: turn.answer.direct_answer,
      rating,
      confidence: turn.answer.confidence,
      document_ids: [...new Set(turn.answer.sources.map((source) => source.document_id))],
    }).catch(() => {
      /* feedback is best-effort; never interrupt the call */
    });
  }

  function submitComment(turn: Turn) {
    if (!turn.answer || !active || !turn.comment.trim()) return;
    updateTurn(active.id, turn.id, { commentSent: true });
    void sendFeedback({
      question: turn.question,
      answer: turn.answer.direct_answer,
      rating: turn.rating ?? "down",
      comment: turn.comment.trim(),
      confidence: turn.answer.confidence,
      document_ids: [...new Set(turn.answer.sources.map((source) => source.document_id))],
    }).catch(() => {});
  }

  const stats = overview?.stats;
  const suggestions = overview?.suggestions ?? [];
  const isEmptyKnowledgeBase = stats?.document_count === 0;
  const turns = active?.turns ?? [];

  // Mid-conversation, a prompt this chat has already used is noise rather than a
  // suggestion, so the strip offers only what is still unasked.
  const unaskedSuggestions = useMemo(() => {
    const asked = new Set(turns.map((turn) => turn.question));
    return suggestions.filter((suggestion) => !asked.has(suggestion));
  }, [suggestions, turns]);

  return (
    <div className="chat-layout">
      <aside className="chat-sidebar">
        <button type="button" className="new-chat-button" onClick={startNewChat}>
          <Plus size={16} strokeWidth={2.5} />
          New chat
        </button>

        <p className="sidebar-heading">Recent</p>
        <ul className="conversation-list">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                className={`conversation-item${conversation.id === active?.id ? " is-active" : ""}`}
                onClick={() => setActiveId(conversation.id)}
              >
                <MessageIcon />
                <span className="conversation-title">{conversation.title}</span>
              </button>
              <button
                type="button"
                className="conversation-delete"
                aria-label={`Delete ${conversation.title}`}
                onClick={() => deleteConversation(conversation.id)}
              >
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>

        <div className="sidebar-footer">
          <p className="sidebar-heading">Knowledge base</p>
          {stats ? (
            <ul className="sidebar-stats">
              <li>
                <BookOpen size={13} />
                {stats.document_count} document{stats.document_count === 1 ? "" : "s"}
              </li>
              <li>
                <ListChecks size={13} />
                {stats.chunk_count} passage{stats.chunk_count === 1 ? "" : "s"}
              </li>
              <li>
                <Sparkles size={13} />
                {stats.model_configured ? stats.model : "AI not configured"}
              </li>
            </ul>
          ) : (
            <p className="sidebar-muted">Not connected</p>
          )}
          <button type="button" className="sidebar-link" onClick={onGoToDocuments}>
            <Upload size={13} />
            Add a document
          </button>
        </div>
      </aside>

      <section className="chat-main">
        <div className="chat-thread">
          {turns.length === 0 ? (
            <div className="chat-welcome">
              <span className="welcome-mark" aria-hidden="true">
                <Sparkles size={22} strokeWidth={2} />
              </span>
              <h1>How can I help on this call?</h1>
              <p className="welcome-sub">
                Ask a policy question in plain English. Every answer is written only from your
                indexed documents, with the sources attached.
              </p>

              {isEmptyKnowledgeBase ? (
                <div className="welcome-empty">
                  <AlertTriangle size={16} />
                  <span>
                    No documents are indexed yet. Convert one on the Documents tab and it becomes
                    searchable here straight away.
                  </span>
                  <button type="button" className="btn btn-primary btn-sm" onClick={onGoToDocuments}>
                    Go to Documents
                  </button>
                </div>
              ) : (
                <div className="welcome-suggestions">
                  <p className="suggestion-label">
                    <Lightbulb size={14} />
                    Suggested questions
                  </p>

                  {overviewStatus === "loading" && (
                    <div className="suggestion-grid" aria-busy="true">
                      {/* Placeholders keep the block at its real height, so the
                          page does not jump when the prompts arrive. */}
                      {[0, 1, 2, 3, 4, 5].map((slot) => (
                        <span key={slot} className="suggestion-card is-skeleton" aria-hidden="true" />
                      ))}
                    </div>
                  )}

                  {overviewStatus === "error" && (
                    <div className="suggestion-notice">
                      <AlertTriangle size={15} />
                      <span>
                        Could not reach the backend, so there are no starter prompts to show. You
                        can still type a question below.
                      </span>
                      <button
                        type="button"
                        className="btn btn-quiet btn-sm"
                        onClick={() => setOverviewAttempt((attempt) => attempt + 1)}
                      >
                        <RotateCw size={13} /> Retry
                      </button>
                    </div>
                  )}

                  {overviewStatus === "ready" &&
                    (suggestions.length > 0 ? (
                      <div className="suggestion-grid">
                        {suggestions.map((suggestion) => (
                          <button
                            key={suggestion}
                            type="button"
                            className="suggestion-card"
                            disabled={isSending}
                            onClick={() => void submitQuestion(suggestion)}
                          >
                            <span>{suggestion}</span>
                            <ChevronRight size={15} />
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="suggestion-notice">
                        <Info size={15} />
                        <span>
                          The indexed documents have no section headings to build starter prompts
                          from. Ask a question below instead.
                        </span>
                      </div>
                    ))}
                </div>
              )}
            </div>
          ) : (
            turns.map((turn) => (
              <article key={turn.id} className="turn">
                <div className="bubble-row is-user">
                  <div className="bubble bubble-user">{turn.question}</div>
                </div>

                <div className="bubble-row is-assistant">
                  <span className="avatar" aria-hidden="true">
                    <Sparkles size={15} strokeWidth={2.2} />
                  </span>

                  {turn.error ? (
                    <div className="bubble bubble-error">
                      <AlertTriangle size={16} />
                      <span>{turn.error}</span>
                    </div>
                  ) : !turn.answer ? (
                    <div className="bubble bubble-assistant">
                      <span className="typing" aria-label="Searching the knowledge base">
                        <i />
                        <i />
                        <i />
                      </span>
                    </div>
                  ) : (
                    <div className="bubble bubble-assistant">
                      <div className="answer-head">
                        <span
                          className={`confidence-pill conf-${turn.answer.confidence.toLowerCase()}`}
                          title={CONFIDENCE_HINT[turn.answer.confidence]}
                        >
                          {turn.answer.confidence} confidence
                        </span>
                        {!turn.answer.answered && (
                          <span className="answer-flag">
                            <Info size={12} /> Not answered from sources
                          </span>
                        )}
                        <span className="answer-latency" title={`${turn.answer.retrieval_ms} ms in retrieval`}>
                          {(turn.answer.latency_ms / 1000).toFixed(1)}s
                        </span>
                      </div>

                      <p className="direct-answer">{turn.answer.direct_answer}</p>

                      {turn.answer.key_details.length > 0 && (
                        <div className="answer-block">
                          <h4>Key details</h4>
                          <ul>
                            {turn.answer.key_details.map((detail) => (
                              <li key={detail}>{detail}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {turn.answer.important_notes.length > 0 && (
                        <div className="answer-block answer-block-warn">
                          <h4>Important notes &amp; exceptions</h4>
                          <ul>
                            {turn.answer.important_notes.map((note) => (
                              <li key={note}>{note}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {turn.answer.suggested_topics.length > 0 && (
                        <div className="answer-block">
                          <h4>Documents I can search</h4>
                          <ul>
                            {turn.answer.suggested_topics.map((topic) => (
                              <li key={topic}>{topic}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {turn.answer.retrieval && (
                        <RetrievalSummary
                          report={turn.answer.retrieval}
                          question={turn.question}
                          onShowTrace={onShowTrace}
                        />
                      )}

                      {turn.answer.sources.length > 0 && (
                        <details className="sources">
                          <summary>
                            <FileText size={14} />
                            {turn.answer.sources.length} source
                            {turn.answer.sources.length === 1 ? "" : "s"}
                            <span className="sources-hint">Agent-only — click to expand</span>
                          </summary>
                          <ol className="source-list">
                            {turn.answer.sources.map((source) => (
                              <li key={source.chunk_id} className={source.cited ? "is-cited" : ""}>
                                <div className="source-head">
                                  <strong>{source.document_title}</strong>
                                  {source.heading && source.heading !== source.document_title && (
                                    <span className="source-section">{source.heading}</span>
                                  )}
                                  {source.cited && <span className="source-used">used</span>}
                                </div>
                                <SourceProvenance source={source} />
                                <pre className="source-excerpt">{source.excerpt}</pre>
                              </li>
                            ))}
                          </ol>
                        </details>
                      )}

                      {turn.answer.follow_ups.length > 0 && (
                        <div className="follow-ups">
                          {turn.answer.follow_ups.map((followUp) => (
                            <button
                              key={followUp}
                              type="button"
                              className="follow-up-chip"
                              disabled={isSending}
                              onClick={() => void submitQuestion(followUp)}
                            >
                              {followUp}
                            </button>
                          ))}
                        </div>
                      )}

                      <div className="answer-actions">
                        <button
                          type="button"
                          className="icon-action"
                          onClick={() => void copyAnswer(turn)}
                        >
                          {copiedTurnId === turn.id ? <Check size={14} /> : <Copy size={14} />}
                          {copiedTurnId === turn.id ? "Copied" : "Copy"}
                        </button>
                        <button
                          type="button"
                          className={`icon-action${turn.rating === "up" ? " is-on" : ""}`}
                          aria-label="Helpful"
                          onClick={() => rate(turn, "up")}
                        >
                          <ThumbsUp size={14} />
                        </button>
                        <button
                          type="button"
                          className={`icon-action${turn.rating === "down" ? " is-on is-down" : ""}`}
                          aria-label="Not helpful"
                          onClick={() => rate(turn, "down")}
                        >
                          <ThumbsDown size={14} />
                        </button>
                        {turn.answer.ai_cost && (
                          <span className="answer-cost">
                            ${turn.answer.ai_cost.total_cost_usd.toFixed(5)}
                          </span>
                        )}
                      </div>

                      {turn.rating === "down" && !turn.commentSent && (
                        <div className="feedback-comment">
                          <input
                            value={turn.comment}
                            placeholder="What was wrong or missing? (optional)"
                            onChange={(event) =>
                              active && updateTurn(active.id, turn.id, { comment: event.target.value })
                            }
                            onKeyDown={(event) => {
                              if (event.key === "Enter") submitComment(turn);
                            }}
                          />
                          <button
                            type="button"
                            className="btn btn-quiet btn-sm"
                            onClick={() => submitComment(turn)}
                          >
                            Send
                          </button>
                        </div>
                      )}
                      {turn.commentSent && (
                        <p className="feedback-thanks">
                          <Check size={13} /> Thanks — logged for content review.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </article>
            ))
          )}
          <div ref={threadEndRef} />
        </div>

        <div className="composer-wrap">
          {/* The welcome screen's prompts disappear with the first question, which
              is exactly when an agent mid-call is most likely to want one. This
              keeps them a click away for the whole conversation, minus whatever
              this chat has already asked. */}
          {turns.length > 0 && unaskedSuggestions.length > 0 && (
            <div className={`composer-suggestions${suggestionsOpen ? " is-open" : ""}`}>
              <button
                type="button"
                className="composer-suggestions-toggle"
                aria-expanded={suggestionsOpen}
                onClick={() => setSuggestionsOpen((open) => !open)}
              >
                <Lightbulb size={13} />
                Suggested questions
                <ChevronDown size={14} className="composer-suggestions-caret" />
              </button>

              {suggestionsOpen && (
                <div className="composer-suggestions-list">
                  {unaskedSuggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className="suggestion-chip"
                      disabled={isSending}
                      onClick={() => void submitQuestion(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="composer">
            <textarea
              ref={composerRef}
              rows={1}
              value={draft}
              placeholder="Ask about a benefit, limit or exclusion…"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
            />
            <button
              type="button"
              className="composer-send"
              aria-label="Send question"
              disabled={!draft.trim() || isSending}
              onClick={() => void submitQuestion(draft)}
            >
              <ArrowUp size={18} strokeWidth={2.6} />
            </button>
          </div>
          <p className="composer-note">
            Answers come only from your indexed documents. Always check the sources before quoting
            figures to a member.
          </p>
        </div>
      </section>
    </div>
  );
}

/** Small inline glyph for the conversation list. */
function MessageIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export default ChatPage;
