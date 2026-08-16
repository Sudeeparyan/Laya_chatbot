import {
  AlertTriangle,
  ArrowLeft,
  Braces,
  ChevronRight,
  ExternalLink,
  FlaskConical,
  Info,
  Layers,
  Link2,
  LoaderCircle,
  Network,
  RefreshCw,
  RotateCcw,
  Route,
  Search,
  SlidersHorizontal,
  Table2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  API_BASE_URL,
  fetchKnowledgeGraph,
  type GraphNodeType,
  type GraphRelation,
  type KnowledgeGraph,
  type RetrievalReport,
} from "../api";
import GraphCanvas, { NodeMark, nodeColor } from "./GraphCanvas";
import {
  NODE_COLOR,
  NODE_TYPE_HINT,
  NODE_TYPE_LABEL,
  NODE_TYPE_ORDER,
  PIPELINE,
  RELATION_COLOR,
  RELATION_DESCRIPTION,
  RELATION_INVERSE_LABEL,
  RELATION_LABEL,
  RELATION_ORDER,
  TRAVERSABLE,
  buildNetwork,
  searchNetwork,
  type NetNode,
} from "./knowledgeGraphModel";

/** The query string that makes the app boot straight into this view. */
export const KNOWLEDGE_GRAPH_ROUTE = "view=knowledge-graph";

/**
 * Where a "open in its own tab" link points.
 *
 * Same origin and path, so it works under the dev server and a built bundle
 * alike.
 */
export function knowledgeGraphUrl(): string {
  return `${window.location.origin}${window.location.pathname}?${KNOWLEDGE_GRAPH_ROUTE}`;
}

/** A node's own mark at legend size, on the dark ground it is drawn against. */
function LegendMark({ node }: { node: NetNode }) {
  return (
    <svg width="18" height="18" viewBox="-9 -9 18 18" aria-hidden="true" className="net-legend-mark">
      <NodeMark node={node} color={nodeColor(node)} scale={9 / Math.max(node.radius, 1)} />
    </svg>
  );
}

/** A representative node per type, so a chip carries the exact mark the canvas draws. */
function useTypeSamples(nodes: NetNode[] | undefined) {
  return useMemo(() => {
    const samples = new Map<GraphNodeType, NetNode>();
    for (const node of nodes ?? []) {
      if (!samples.has(node.type)) samples.set(node.type, node);
    }
    return samples;
  }, [nodes]);
}

type KnowledgeGraphViewProps = {
  /**
   * A retrieval trace to replay, handed over from the chat page.
   *
   * When present the canvas dims everything that was not on the walk, so the
   * question "why did the answer cite that passage?" is answered by looking at
   * it rather than by reading a log.
   */
  trace?: RetrievalReport | null;
  traceQuestion?: string | null;
  onClearTrace?: () => void;
  /**
   * The way back to wherever the graph was opened from.
   *
   * The graph is no longer a section of the app in its own right — it is
   * opened from the Documents page or from an answer, and it owes the caller a
   * way home. Absent on the standalone route, which owns its whole tab.
   */
  onExit?: () => void;
  exitLabel?: string;
};

function KnowledgeGraphView({
  trace,
  traceQuestion,
  onClearTrace,
  onExit,
  exitLabel = "Back",
}: KnowledgeGraphViewProps) {
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [activeDocument, setActiveDocument] = useState<string | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<GraphNodeType>>(new Set());
  /**
   * Concept co-occurrence starts collapsed.
   *
   * It is by far the densest relation — roughly a third of all edges — and it
   * connects concepts to other concepts, so drawing it first buries the
   * document → section → chunk → term backbone that the graph is actually about
   * under a mesh of pink. It is one click away in the Relations list, with its
   * full count showing, and turning it on is how the community structure
   * becomes visible. Defaulting it off changes what you see first, not what
   * exists.
   */
  const [hiddenRelations, setHiddenRelations] = useState<Set<GraphRelation>>(
    () => new Set<GraphRelation>(["co_occurs"]),
  );
  const [mode, setMode] = useState<"graph" | "table">("graph");
  const [showMethod, setShowMethod] = useState(false);
  /**
   * The pipeline stage being lit up, if any.
   *
   * This highlights rather than filters. Hiding everything but one stage would
   * empty the canvas for four of the six — concepts and communities are only
   * drawn while something they touch is still on screen — and, more to the
   * point, the question a stage answers is "where do these sit in the whole
   * thing?", which cannot be answered by a picture with the whole thing
   * removed from it.
   */
  const [stageFocus, setStageFocus] = useState<string | null>(null);
  const [visible, setVisible] = useState({ nodes: 0, edges: 0 });
  const [resetSignal, setResetSignal] = useState(0);
  /**
   * Node the camera should fly to.
   *
   * Picking a node off the search list or the panel means asking "where is
   * this?", so the camera goes there. Clicking a node in the scene does not —
   * you are already looking at it, and moving the camera under the cursor is
   * disorienting. The counter makes repeat picks of the same node re-fly.
   */
  const [flyTo, setFlyTo] = useState<{ id: string; nonce: number } | null>(null);

  /** Select and travel — for the search list and the connected list. */
  const selectAndFly = useCallback((id: string) => {
    setSelectedNodeId(id);
    setFlyTo((previous) => ({ id, nonce: (previous?.nonce ?? 0) + 1 }));
  }, []);

  const load = useCallback(() => {
    setError(null);
    fetchKnowledgeGraph()
      .then(setGraph)
      .catch((caught) =>
        setError(caught instanceof Error ? caught.message : "Could not load the knowledge graph."),
      );
  }, []);

  useEffect(load, [load]);

  // Structure only — positions belong to the 3D engine. Memoised on the
  // snapshot so a filter or a selection never rebuilds the network and
  // restarts the simulation from scratch.
  const net = useMemo(() => (graph ? buildNetwork(graph) : null), [graph]);
  const samples = useTypeSamples(net?.nodes);

  const traceNodes = useMemo(
    () => (trace && trace.highlight_nodes.length ? new Set(trace.highlight_nodes) : null),
    [trace],
  );

  const selectedNode = selectedNodeId && net ? net.byId.get(selectedNodeId) ?? null : null;

  /** Neighbours of the selection, grouped in legend order for the panel. */
  const connected = useMemo(() => {
    if (!net || !selectedNodeId) return [];
    const order = Object.fromEntries(RELATION_ORDER.map((kind, index) => [kind, index]));
    return [...(net.neighbours.get(selectedNodeId) ?? [])]
      .map((item) => ({ ...item, node: net.byId.get(item.id) }))
      .filter((item): item is typeof item & { node: NetNode } => Boolean(item.node))
      .sort((a, b) => order[a.kind] - order[b.kind] || a.node.title.localeCompare(b.node.title));
  }, [net, selectedNodeId]);

  /** Canvas dimming: every node the query touches, or null when idle. */
  const searchIds = useMemo(() => {
    if (!net) return null;
    const needle = query.trim().toLowerCase();
    if (!needle) return null;
    const set = new Set<string>();
    for (const node of net.nodes) {
      if (node.haystack.includes(needle)) set.add(node.id);
    }
    return set;
  }, [net, query]);

  const searchHits = useMemo(() => (net ? searchNetwork(net, query) : []), [net, query]);

  /** Every node belonging to the lit pipeline stage. */
  const stageIds = useMemo(() => {
    if (!net || !stageFocus) return null;
    const stage = PIPELINE.find((item) => item.id === stageFocus);
    if (!stage) return null;
    const types = new Set(stage.types);
    const ids = new Set<string>();
    for (const node of net.nodes) {
      if (types.has(node.type)) ids.add(node.id);
    }
    return ids;
  }, [net, stageFocus]);

  /** Node counts per stage, for the rail. */
  const stageCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const stage of PIPELINE) {
      counts[stage.id] = stage.types.reduce(
        (total, type) => total + (net?.typeCounts[type] ?? 0),
        0,
      );
    }
    return counts;
  }, [net]);

  const toggleType = useCallback((type: GraphNodeType) => {
    setHiddenTypes((previous) => {
      const next = new Set(previous);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }, []);

  const toggleRelation = useCallback((kind: GraphRelation) => {
    setHiddenRelations((previous) => {
      const next = new Set(previous);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const resetView = useCallback(() => {
    setSelectedNodeId(null);
    setQuery("");
    setActiveDocument(null);
    setStageFocus(null);
    setHiddenTypes(new Set());
    // Back to the readable default, not to "everything on" — reset should
    // restore the view you started from.
    setHiddenRelations(new Set<GraphRelation>(["co_occurs"]));
    setFlyTo(null);
    setResetSignal((signal) => signal + 1);
  }, []);

  const handleVisibleChange = useCallback((counts: { nodes: number; edges: number }) => {
    setVisible((previous) =>
      previous.nodes === counts.nodes && previous.edges === counts.edges ? previous : counts,
    );
  }, []);

  if (error) {
    return (
      <div className="graph-overlay is-page" aria-label="Knowledge graph">
        <div className="graph-shell graph-shell-message">
          <div className="error-banner">
            <AlertTriangle size={18} />
            <span>{error}</span>
          </div>
          <p className="graph-hint">
            Compile one with <code>python backend/scripts/build_knowledge_graph.py</code>, then
            reload this view.
          </p>
          <button className="btn btn-quiet btn-sm" type="button" onClick={load}>
            <RefreshCw size={14} /> Try again
          </button>
        </div>
      </div>
    );
  }

  if (!graph || !net) {
    return (
      <div className="graph-overlay is-page" aria-label="Knowledge graph">
        <div className="graph-shell graph-shell-message">
          <LoaderCircle className="spin" size={26} />
          <p>Loading the compiled knowledge graph…</p>
        </div>
      </div>
    );
  }

  const { stats } = graph;

  return (
    <div className="graph-overlay is-page" aria-label="Knowledge graph">
      <div className="graph-shell is-page">
        <header className="graph-head">
          {onExit && (
            <button type="button" className="graph-exit" onClick={onExit}>
              <ArrowLeft size={16} />
              {exitLabel}
            </button>
          )}

          <div className="graph-title">
            <span className="graph-title-mark" aria-hidden="true">
              <Network size={18} />
            </span>
            <div>
              <p className="eyebrow">What retrieval walks</p>
              <h2>Knowledge graph</h2>
            </div>
          </div>

          <div className="graph-head-stats">
            <span>
              <strong>{stats.nodes}</strong> nodes
            </span>
            <span>
              <strong>{stats.edges}</strong> relations
            </span>
            <span>
              <strong>{stats.chunks}</strong> chunks
            </span>
            <span>
              <strong>{stats.concepts}</strong> concepts
            </span>
            <span>
              <strong>{stats.communities}</strong> communities
            </span>
            <span className="graph-rev">{graph.corpus_fingerprint}</span>
          </div>

          <div className="graph-head-actions">
            {onExit && (
              <a
                className="btn btn-quiet btn-sm"
                href={knowledgeGraphUrl()}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={15} />
                New tab
              </a>
            )}
            <a
              className="btn btn-quiet btn-sm"
              href={`${API_BASE_URL}/api/graph/knowledge`}
              target="_blank"
              rel="noreferrer"
            >
              <Braces size={15} />
              Raw JSON
            </a>
          </div>
        </header>

        {/* The pipeline, in the order the system builds it. Reading left to
            right is the whole application end to end: a file is converted,
            split at its headings, cut into passages, mined for terms, and the
            terms are clustered — and each step lights up where it lives in the
            scene. */}
        <nav className="graph-pipeline" aria-label="Corpus pipeline">
          {PIPELINE.map((stage, index) => {
            const lit = stageFocus === stage.id;
            return (
              <div className="graph-pipeline-step" key={stage.id}>
                {index > 0 && (
                  <ChevronRight className="graph-pipeline-arrow" size={15} aria-hidden="true" />
                )}
                <button
                  type="button"
                  className={`graph-stage${lit ? " is-lit" : ""}`}
                  aria-pressed={lit}
                  onClick={() => setStageFocus(lit ? null : stage.id)}
                  title={`${stage.caption} — click to light these up in the scene`}
                >
                  <span className="graph-stage-swatches" aria-hidden="true">
                    {stage.types.map((type) => (
                      <span
                        key={type}
                        className="graph-stage-dot"
                        style={{ background: NODE_COLOR[type] }}
                      />
                    ))}
                  </span>
                  <span className="graph-stage-text">
                    <span className="graph-stage-label">
                      {stage.label}
                      <span className="graph-stage-count">{stageCounts[stage.id]}</span>
                    </span>
                    <span className="graph-stage-caption">{stage.caption}</span>
                  </span>
                </button>
              </div>
            );
          })}
          {stageFocus && (
            <button type="button" className="graph-stage-clear" onClick={() => setStageFocus(null)}>
              <X size={13} /> Clear
            </button>
          )}
        </nav>

        {graph.stale && (
          <div className="graph-stale-banner">
            <AlertTriangle size={15} />
            <span>
              This snapshot was compiled from a different corpus than the one currently indexed —
              a document has been added or changed since. Rebuild with{" "}
              <code>python backend/scripts/build_knowledge_graph.py</code>.
            </span>
          </div>
        )}

        {trace && traceNodes && (
          <div className="graph-trace-banner">
            <Route size={15} />
            <span>
              Replaying retrieval for{" "}
              <strong>{traceQuestion ? `“${traceQuestion}”` : "the last answer"}</strong> —{" "}
              {trace.seed_count} seed {trace.seed_count === 1 ? "passage" : "passages"},{" "}
              {trace.expanded_count} added by the graph, {traceNodes.size} nodes on the path.
              {trace.linked_concepts.length > 0 && (
                <> Concepts matched: {trace.linked_concepts.slice(0, 4).join(", ")}.</>
              )}
            </span>
            {onClearTrace && (
              <button type="button" className="btn btn-quiet btn-sm" onClick={onClearTrace}>
                <X size={14} /> Clear
              </button>
            )}
          </div>
        )}

        <div className="graph-toolbar">
          <div className="graph-search">
            <Search size={15} />
            <input
              value={query}
              placeholder="Search documents, passages, concepts, amounts…"
              onChange={(event) => setQuery(event.target.value)}
            />
            {mode === "graph" && searchHits.length > 0 && (
              <ul className="graph-search-results">
                {searchHits.map((hit) => (
                  <li key={hit.id}>
                    <button type="button" onClick={() => selectAndFly(hit.id)}>
                      <span className="net-result-dot" style={{ background: nodeColor(hit) }} />
                      <span className="net-result-title">{hit.title}</span>
                      <span className="net-result-kind">{NODE_TYPE_LABEL[hit.type]}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <select
            className="graph-domain-select"
            value={activeDocument ?? ""}
            onChange={(event) => setActiveDocument(event.target.value || null)}
            aria-label="Filter by document"
          >
            <option value="">All {stats.documents} documents</option>
            {net.documents.map((doc) => (
              <option key={doc.id} value={doc.id}>
                {doc.title}
              </option>
            ))}
          </select>

          <div className="graph-mode" role="tablist" aria-label="View mode">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "graph"}
              className={`graph-mode-tab${mode === "graph" ? " is-active" : ""}`}
              onClick={() => setMode("graph")}
            >
              <Network size={14} /> 3D graph
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "table"}
              className={`graph-mode-tab${mode === "table" ? " is-active" : ""}`}
              onClick={() => setMode("table")}
            >
              <Table2 size={14} /> Communities
            </button>
          </div>
        </div>

        {/* The panel used to flip between a light and a dark treatment here,
            depending on the view mode. It no longer does — both halves sit on
            the same ground — so the body needs no modifier. */}
        <div className="graph-body">
          {mode === "graph" ? (
            <GraphCanvas
              net={net}
              selectedId={selectedNodeId}
              onSelect={setSelectedNodeId}
              hiddenTypes={hiddenTypes}
              hiddenRelations={hiddenRelations}
              activeDocument={activeDocument}
              matchedIds={searchIds ?? stageIds}
              traceNodes={traceNodes}
              onVisibleChange={handleVisibleChange}
              resetSignal={resetSignal}
              flyToId={flyTo?.id ?? null}
            />
          ) : (
            <div className="graph-table-wrap">
              <table className="graph-table">
                <caption>
                  Concept communities found by label propagation over the co-occurrence graph.
                  Nothing here was named by hand — each label is the community's three most widely
                  attested concepts.
                </caption>
                <thead>
                  <tr>
                    <th>Community</th>
                    <th>Concepts</th>
                    <th>Chunks</th>
                    <th>Members</th>
                  </tr>
                </thead>
                <tbody>
                  {graph.communities
                    .filter((community) => {
                      const needle = query.trim().toLowerCase();
                      if (!needle) return true;
                      return (
                        community.label.toLowerCase().includes(needle) ||
                        community.members.some((member) => member.toLowerCase().includes(needle))
                      );
                    })
                    .map((community) => (
                      <tr
                        key={community.id}
                        className={selectedNodeId === community.id ? "is-selected" : undefined}
                        onClick={() => selectAndFly(community.id)}
                      >
                        <td>
                          <strong>{community.label}</strong>
                        </td>
                        <td>{community.concept_count}</td>
                        <td>{community.chunk_count}</td>
                        <td className="cell-muted">{community.members.slice(0, 10).join(" · ")}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}

          <aside className="graph-panel">
            {selectedNode ? (
              <>
                <button
                  className="net-back-btn"
                  type="button"
                  onClick={() => setSelectedNodeId(null)}
                >
                  ← Back to {mode === "graph" ? "filters" : "overview"}
                </button>

                <div className="graph-panel-head">
                  <div>
                    <p className="eyebrow">{NODE_TYPE_LABEL[selectedNode.type]}</p>
                    <h3>{selectedNode.title}</h3>
                  </div>
                  <LegendMark node={selectedNode} />
                </div>

                <p className="graph-statement">{selectedNode.meta}</p>

                {/* Per-type detail. Only the fields a given type actually
                    carries are shown, so no row ever reads "—". */}
                {selectedNode.type === "chunk" && (
                  <>
                    <dl className="graph-facts">
                      <dt>From</dt>
                      <dd>
                        {selectedNode.document_title} → {selectedNode.heading}
                      </dd>
                      <dt>Size</dt>
                      <dd>
                        {selectedNode.char_count?.toLocaleString()} characters ·{" "}
                        {selectedNode.token_count} indexed terms
                      </dd>
                      <dt>Chunk id</dt>
                      <dd>
                        <code className="graph-condition">{selectedNode.id}</code>
                      </dd>
                    </dl>
                    <h4 className="graph-panel-heading">Passage text</h4>
                    <pre className="graph-excerpt">{selectedNode.excerpt}</pre>
                  </>
                )}

                {selectedNode.type === "document" && (
                  <dl className="graph-facts">
                    <dt>Source</dt>
                    <dd>
                      {selectedNode.source_filename} ({selectedNode.source_type})
                    </dd>
                    <dt>Department</dt>
                    <dd>{selectedNode.department ?? "not recorded"}</dd>
                    <dt>Classification</dt>
                    <dd>{selectedNode.classification}</dd>
                    <dt>Access group</dt>
                    <dd>{selectedNode.access_group ?? "not recorded"}</dd>
                  </dl>
                )}

                {(selectedNode.type === "concept" ||
                  selectedNode.type === "value" ||
                  selectedNode.type === "constraint") && (
                  <dl className="graph-facts">
                    <dt>Appears in</dt>
                    <dd>
                      {selectedNode.chunk_frequency} chunk
                      {selectedNode.chunk_frequency === 1 ? "" : "s"}
                    </dd>
                    <dt>Specificity</dt>
                    <dd>
                      {selectedNode.specificity?.toFixed(2)}
                      <span className="graph-facts-note">
                        {" "}
                        — how much relevance survives a step through this term. A term in one
                        passage scores near 1; one in half the corpus scores near 0.
                      </span>
                    </dd>
                    <dt>Found by</dt>
                    <dd>
                      {selectedNode.extraction === "c-value"
                        ? "C-value term extraction"
                        : `pattern (${selectedNode.subtype?.replace("_", " ")})`}
                    </dd>
                  </dl>
                )}

                {selectedNode.type === "community" && (
                  <>
                    <dl className="graph-facts">
                      <dt>Size</dt>
                      <dd>
                        {selectedNode.concept_count} concepts across {selectedNode.chunk_count}{" "}
                        chunks
                      </dd>
                    </dl>
                    <h4 className="graph-panel-heading">Members</h4>
                    <div className="net-filter-chips">
                      {(selectedNode.members ?? []).map((member) => (
                        <span className="fact-chip" key={member}>
                          {member}
                        </span>
                      ))}
                    </div>
                  </>
                )}

                <h4 className="graph-panel-heading">
                  <Link2 size={14} /> Connected ({connected.length})
                </h4>
                <ul className="net-connected">
                  {connected.map((item) => (
                    <li key={`${item.kind}:${item.id}`}>
                      <button type="button" onClick={() => selectAndFly(item.id)}>
                        <span
                          className="net-relation-swatch"
                          style={{ background: RELATION_COLOR[item.kind] }}
                          aria-hidden="true"
                        />
                        <span className="net-connected-relation">
                          {item.outgoing
                            ? RELATION_LABEL[item.kind]
                            : RELATION_INVERSE_LABEL[item.kind]}
                        </span>
                        <span className="net-connected-title">{item.node.title}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <>
                {mode === "graph" && (
                  <>
                    <div className="graph-panel-head">
                      <div>
                        <p className="eyebrow">
                          {visible.nodes} of {net.nodes.length} nodes · {visible.edges} of{" "}
                          {net.edges.length} relations
                        </p>
                        <h3>
                          <SlidersHorizontal size={15} /> Filters
                        </h3>
                      </div>
                    </div>

                    <h4 className="graph-panel-heading">Node types</h4>
                    <div className="net-filter-chips">
                      {NODE_TYPE_ORDER.map((type) => {
                        const sample = samples.get(type);
                        const off = hiddenTypes.has(type);
                        return (
                          <button
                            key={type}
                            type="button"
                            className={`net-filter-chip${off ? " is-off" : ""}`}
                            onClick={() => toggleType(type)}
                            aria-pressed={!off}
                            title={NODE_TYPE_HINT[type]}
                          >
                            {sample && <LegendMark node={sample} />}
                            <span className="net-chip-name">{NODE_TYPE_LABEL[type]}</span>
                            <span className="net-chip-count">{net.typeCounts[type]}</span>
                          </button>
                        );
                      })}
                    </div>

                    <h4 className="graph-panel-heading">Relations</h4>
                    <div className="net-filter-chips">
                      {RELATION_ORDER.map((kind) => {
                        const off = hiddenRelations.has(kind);
                        return (
                          <button
                            key={kind}
                            type="button"
                            className={`net-filter-chip${off ? " is-off" : ""}`}
                            onClick={() => toggleRelation(kind)}
                            aria-pressed={!off}
                            title={RELATION_DESCRIPTION[kind]}
                          >
                            <span
                              className="net-relation-swatch"
                              style={{ background: RELATION_COLOR[kind] }}
                              aria-hidden="true"
                            />
                            <span className="net-chip-name">
                              {RELATION_LABEL[kind]}
                              {!TRAVERSABLE[kind] && (
                                <span className="net-chip-tag" title="Drawn, but never walked during retrieval">
                                  structural
                                </span>
                              )}
                            </span>
                            <span className="net-chip-count">{net.counts[kind]}</span>
                          </button>
                        );
                      })}
                    </div>

                    <button className="net-reset-btn" type="button" onClick={resetView}>
                      <RotateCcw size={14} /> Reset view
                    </button>
                  </>
                )}

                <div className="graph-panel-head">
                  <div>
                    <p className="eyebrow">Compiled {graph.generated_at_utc.slice(0, 10)}</p>
                    <h3>How to read this</h3>
                  </div>
                </div>

                <ul className="graph-key">
                  <li>
                    Every node and edge here was <strong>derived from the corpus</strong> — no rule
                    pack, no hand-written ontology, no model call. The same snapshot the retriever
                    walks is the one drawn here, so the picture and the retrieval are never two
                    different things.
                  </li>
                  <li>
                    <strong>Chunks</strong> are the units an answer cites. <strong>Concepts</strong>{" "}
                    were mined by C-value; <strong>values</strong> and{" "}
                    <strong>constraints</strong> by pattern. <strong>Communities</strong> came from
                    label propagation.
                  </li>
                  <li>
                    Two relations are marked <strong>structural</strong>: they are true and are
                    drawn, but retrieval never walks them. Stepping through a document or a
                    department would make every passage in it relevant to every other, which is not
                    retrieval.
                  </li>
                  <li>
                    Hovering a node <strong>dims everything it is not connected to</strong>, which
                    is the quickest way to read one passage's reach. Clicking one keeps that view
                    and sends <strong>beads travelling along its relations</strong>, in the
                    direction the relation is stored.
                  </li>
                  <li>
                    The row of steps along the top is the corpus{" "}
                    <strong>as the system builds it</strong>. Clicking a step lights up that layer
                    everywhere it appears, which is how you find out where the passages of one
                    document actually sit.
                  </li>
                </ul>

                <h4 className="graph-panel-heading">
                  <Layers size={14} /> Corpus
                </h4>
                <ul className="graph-vocab">
                  {graph.documents.map((doc) => (
                    <li key={doc.document_id}>
                      <span className="vocab-label">{doc.title}</span>
                      <span className="vocab-values">
                        {doc.department} · {doc.chunk_count} chunks
                      </span>
                    </li>
                  ))}
                </ul>

                <button
                  className="btn btn-quiet btn-sm graph-notice-toggle"
                  type="button"
                  onClick={() => setShowMethod(!showMethod)}
                >
                  <FlaskConical size={14} />
                  {showMethod ? "Hide" : "Show"} extraction method
                </button>
                {showMethod && (
                  <ul className="graph-method">
                    {Object.entries(graph.method).map(([stage, description]) => (
                      <li key={stage}>
                        <strong>{stage.replace(/_/g, " ")}</strong>
                        <span>{description}</span>
                      </li>
                    ))}
                  </ul>
                )}

                {graph.is_mock_corpus && (
                  <p className="graph-mock-notice">
                    <Info size={13} /> {graph.corpus_notice}
                  </p>
                )}
              </>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}

export default KnowledgeGraphView;
