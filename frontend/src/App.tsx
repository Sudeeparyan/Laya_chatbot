import { FileText, MessagesSquare } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import ChatPage from "./pages/ChatPage";
import DocumentsPage from "./pages/DocumentsPage";
import KnowledgeGraphView, { KNOWLEDGE_GRAPH_ROUTE } from "./pages/KnowledgeGraphView";
import { fetchKnowledgeOverview, type KnowledgeStats, type RetrievalReport } from "./api";

/**
 * The graph is a view but not a section.
 *
 * It is opened from the Documents page, where the things it draws are the
 * things you just uploaded, or from an answer that wants its retrieval walk
 * replayed. It is not a place you navigate to on its own, so it is deliberately
 * absent from `TABS` — and it always carries a way back to whichever of the two
 * sent you there.
 */
type View = "chat" | "documents" | "graph";

/** The section the graph returns to when it is closed. */
type Section = Exclude<View, "graph">;

/**
 * The graph can also be opened in a tab of its own, which is the comfortable
 * way to read a 500-node network on a small screen. There is no router here, so
 * the one route the app understands is read straight off the query string at
 * startup — it never changes while the tab is open.
 */
function wantsGraphTab(): boolean {
  const [key, value] = KNOWLEDGE_GRAPH_ROUTE.split("=");
  return new URLSearchParams(window.location.search).get(key) === value;
}

const TABS: { id: Section; label: string; icon: typeof MessagesSquare }[] = [
  { id: "chat", label: "Knowledge Assistant", icon: MessagesSquare },
  { id: "documents", label: "Documents", icon: FileText },
];

function App() {
  // Read once: a tab opened on this route is only ever the graph.
  const [standaloneGraph] = useState(wantsGraphTab);
  // Chat is the landing view.
  const [view, setView] = useState<View>("chat");
  /** Where closing the graph goes back to. */
  const [returnTo, setReturnTo] = useState<Section>("chat");
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  /** Bumped after a conversion so the chat page re-reads the knowledge base. */
  const [knowledgeVersion, setKnowledgeVersion] = useState(0);
  /**
   * The retrieval trace being replayed on the graph, if any.
   *
   * The graph is a tab rather than a separate window precisely so this can be
   * ordinary React state: "show me why the answer cited that" is one click from
   * the chat, with no serialising a trace through a URL or storage.
   */
  const [trace, setTrace] = useState<{ report: RetrievalReport; question: string } | null>(null);

  const refreshKnowledge = useCallback(() => setKnowledgeVersion((n) => n + 1), []);

  const showTraceInGraph = useCallback((report: RetrievalReport, question: string) => {
    setTrace({ report, question });
    setReturnTo("chat");
    setView("graph");
  }, []);

  const openGraphFromDocuments = useCallback(() => {
    setTrace(null);
    setReturnTo("documents");
    setView("graph");
  }, []);

  const closeGraph = useCallback(() => {
    setTrace(null);
    setView(returnTo);
  }, [returnTo]);

  useEffect(() => {
    if (standaloneGraph) return;
    fetchKnowledgeOverview()
      .then((overview) => setStats(overview.stats))
      .catch(() => setStats(null));
  }, [knowledgeVersion, standaloneGraph]);

  // The standalone route owns the whole tab: no shell, no nav, nothing to
  // close back to, and no trace to replay.
  if (standaloneGraph) {
    return <KnowledgeGraphView />;
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-text">
            <strong>Knowledge Hub</strong>
            <small>Agent Assist</small>
          </span>
        </div>

        <nav className="topnav" aria-label="Main sections">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className={`topnav-tab${view === id ? " is-active" : ""}`}
              aria-current={view === id ? "page" : undefined}
              onClick={() => setView(id)}
            >
              <Icon size={17} strokeWidth={2} />
              {label}
            </button>
          ))}
        </nav>

        <div className="topbar-status">
          {stats ? (
            <>
              <span className={`status-dot${stats.model_configured ? " is-ready" : " is-off"}`} />
              <span>
                {stats.document_count} document{stats.document_count === 1 ? "" : "s"} indexed
              </span>
            </>
          ) : (
            <span className="muted">Connecting…</span>
          )}
        </div>
      </header>

      {/* Chat and Documents stay mounted so chat history and conversion results
          survive a tab switch. The graph is not: it holds a WebGL context and a
          running force simulation, and keeping those alive behind another tab
          costs a great deal for something nobody is looking at. */}
      <main className="app-main">
        <div style={{ display: view === "chat" ? "block" : "none" }}>
          <ChatPage
            knowledgeVersion={knowledgeVersion}
            onGoToDocuments={() => setView("documents")}
            onShowTrace={showTraceInGraph}
          />
        </div>
        <div style={{ display: view === "documents" ? "block" : "none" }}>
          <DocumentsPage
            knowledgeVersion={knowledgeVersion}
            onKnowledgeChanged={refreshKnowledge}
            onOpenGraph={openGraphFromDocuments}
          />
        </div>
        {view === "graph" && (
          <KnowledgeGraphView
            trace={trace?.report ?? null}
            traceQuestion={trace?.question ?? null}
            onClearTrace={() => setTrace(null)}
            onExit={closeGraph}
            exitLabel={returnTo === "chat" ? "Back to the answer" : "Back to Documents"}
          />
        )}
      </main>
    </div>
  );
}

export default App;
