# Frontend

## Overview

Single-page React app built with Vite and TypeScript. It has two sections and one view:

| Surface | File | What it is |
|---------|------|------------|
| Knowledge Assistant | `pages/ChatPage.tsx` | Grounded RAG chat with sources, feedback and retrieval-mode switching. The landing view. |
| Documents | `pages/DocumentsPage.tsx` | Upload, pre-analysis, metadata capture, Markdown preview, confidence, Clean Data tab |
| Knowledge graph | `pages/KnowledgeGraphView.tsx` | Graph explorer and retrieval-trace replay |

The graph is deliberately **not** a tab. It is opened from Documents (to see the
structure behind what you just uploaded) or from an answer (to replay the walk
that retrieved it), and it always carries a way back to whichever sent you
there. It can also be opened standalone at `?view=knowledge-graph`, which is the
comfortable way to read a large network on a small screen.

Chat and Documents stay mounted across a switch so chat history and conversion
results survive. The graph does not — it holds a WebGL context and a running
force simulation, which are expensive to keep alive behind another tab.

**Entry point:** `frontend/src/App.tsx`

**Dev server:** `http://127.0.0.1:5173`

**Backend API base:** `VITE_API_BASE_URL` env var, defaults to `http://127.0.0.1:8000`

---

## Shell state (`App.tsx`)

| State | Type | Purpose |
|---|---|---|
| `standaloneGraph` | `boolean` | Read once at startup from the query string; when set, the graph owns the whole tab |
| `view` | `"chat" \| "documents" \| "graph"` | Active surface |
| `returnTo` | `"chat" \| "documents"` | Where closing the graph goes back to |
| `stats` | `KnowledgeStats \| null` | Corpus stats for the top-bar indicator |
| `knowledgeVersion` | `number` | Bumped after a conversion so the chat re-reads the knowledge base |
| `trace` | `{ report, question } \| null` | The retrieval trace being replayed on the graph |

Passing the trace as ordinary React state is the reason the graph is a view
rather than a separate window: "show me why the answer cited that" is one click
from the chat, with no serialising a trace through a URL or storage.

---

## API calls

| Action | Endpoint | Trigger |
|--------|----------|---------|
| Corpus stats | `GET /api/knowledge/overview` | On mount and after every conversion |
| Ask a question | `POST /api/chat` | Chat submit — carries `mode` (`core` / `graph`) |
| Read a source in full | `GET /api/knowledge/documents/{id}` | Clicking a citation |
| Rate an answer | `POST /api/chat/feedback` | Thumbs up/down |
| Load the graph | `GET /api/graph/knowledge` | On opening the graph view |
| Fetch capabilities | `GET /api/capabilities` | Documents page mount |
| Pre-analyze file | `POST /api/analyze` | On file selection (drag/drop or browse) |
| Convert document | `POST /api/convert` | On form submit |
| Run cleaner | `POST /api/clean` | **Run cleaner** in the Clean Data tab |
| Save cleaned Markdown | `POST /api/clean/save` | **Save as canonical** in the Clean Data tab |

---

## Documents page

### Form fields (sent to `/api/convert`)

| Field | Maps To | Notes |
|-------|---------|-------|
| `document_title` | metadata.document_title | Optional; defaults to filename stem |
| `department` | metadata.department | Free text |
| `access_group` | metadata.access_group | Free text |
| `classification` | metadata.classification | Select: General / Internal / Confidential / Restricted |
| `document_owner` | metadata.document_owner | Optional |
| `additional_context` | metadata.additional_context | Large textarea; most important for undescribed files |
| `version` | metadata.version | Optional |
| `expiry_review_date` | metadata.expiry_review_date | Date picker |
| `enable_plugin` | AI plugin toggle | Blocked by UI for Confidential/Restricted |

### Sections

| Section | Condition | Description |
|---------|-----------|-------------|
| Upload zone | always | Drag and drop or browse. Shows file analysis result inline after selection. |
| Metadata form | after file selected | Fields for department, classification, context, etc. |
| AI plugin toggle | when plugin available | Disabled for Confidential/Restricted files. |
| Output tab | after conversion | Markdown preview, copy, download, confidence breakdown, warnings |
| Clean Data tab | after conversion | Toggleable cleaning steps, **Run cleaner** + **Save as canonical**, AI summary block, per-step audit log, raw vs. cleaned size deltas, full cleaned Markdown preview. The AI summary toggle is auto-disabled for Confidential/Restricted classifications. |
| Explore knowledge graph | always | Opens the graph view on the current corpus |
| Capabilities panel | toggled by header button | What the system can/cannot do, plugin status, supported file types |

---

## Chat page

Every answer renders its evidence, not just its text: numbered sources, the
document each came from, and — in `graph` mode — why each passage was retrieved
(`seed` vs `expanded`, hop count, and the shared term that brought it in).

| Element | Behaviour |
|---------|-----------|
| Mode switch | `core` (BM25 only) or `graph` (graph-expanded). Sent as `mode` on `/api/chat`. |
| Sources list | Click to read the full document via `/api/knowledge/documents/{id}` |
| Fallback state | When `answered` is `false`, the reason is shown rather than an empty answer |
| Fell-back notice | Shown when `graph` was requested but no snapshot exists and `core` ran instead |
| Replay trace | Opens the graph view with the answer's `RetrievalReport` highlighted |
| Feedback | Thumbs up/down, with optional comment, to `/api/chat/feedback` |

---

## Knowledge graph view

| File | Responsibility |
|------|----------------|
| `pages/KnowledgeGraphView.tsx` | Data loading, filters, legend, node inspector, trace replay, stale banner |
| `pages/GraphCanvas.tsx` | Rendering only — `3d-force-graph` over `three`, plus bloom post-processing |
| `pages/knowledgeGraphModel.ts` | Types, projection from the API payload, filtering and layout helpers |

The split is intentional: the model layer is pure and testable, the canvas is
rendering with no knowledge of what the nodes mean, and the view holds the
state. Node and edge types match the backend schema exactly — 8 node types and 8
relations, documented in [services.md](services.md#graph_builderpy).

When the snapshot's `corpus_fingerprint` differs from the indexed corpus the API
sets `stale`, and the view says so rather than quietly drawing a picture of a
corpus that has moved on.

---

## TypeScript types

Types live in `frontend/src/api.ts` and mirror the backend Pydantic models:

| Type | Mirrors |
|------|---------|
| `ConversionResponse` | `models.ConversionResponse` |
| `ConversionMetadata` | `models.ConversionMetadata` |
| `ConfidenceScore` | `models.ConfidenceScore` |
| `CapabilityItem` / `CapabilitiesResponse` | `models.CapabilityItem` / `models.CapabilitiesResponse` |
| `FileAnalysis` | `models.FileAnalysisResponse` |
| `CleanOptions` / `CleanResult` / `CleaningStep` | `models.CleanOptions` / `models.CleanResult` / `models.CleaningStep` |
| `ChatResponse` / `ChatSource` | `models.ChatResponse` / `models.ChatSource` |
| `RetrievalReport` | `models.RetrievalReport` |
| `KnowledgeStats` / `KnowledgeDocument` | `models.KnowledgeStats` / `models.KnowledgeDocument` |

Graph node and edge types are declared separately in `pages/knowledgeGraphModel.ts`.

---

## Build & dev commands

```powershell
# Install dependencies
cd frontend
npm install

# Dev server (hot reload)
npm run dev

# Production build (type-checks first: `tsc -b && vite build`)
npm run build

# Preview production build
npm run preview
```

**Vite config:** `frontend/vite.config.ts`

**TypeScript config:** `frontend/tsconfig.json` + `frontend/tsconfig.node.json`
(the latter is a referenced composite project, so it emits to
`node_modules/.tmp/` rather than beside `vite.config.ts`)
