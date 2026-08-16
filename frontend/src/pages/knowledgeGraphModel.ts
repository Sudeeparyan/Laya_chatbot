import type {
  GraphNodeType,
  GraphRelation,
  KnowledgeEdge,
  KnowledgeGraph,
  KnowledgeNode,
} from "../api";

// ---------------------------------------------------------------------------
// Presentation model for the compiled knowledge graph.
//
// The backend already decided what the nodes and edges are — see
// `backend/app/services/graph_builder.py`. This module adds only what drawing
// needs: a colour and a size per node, a colour per relation, the adjacency the
// detail panel reads, and a search index.
//
// Nothing here invents structure. That distinction matters: the view this
// replaced derived four of its five relation types in the browser, so the
// picture on screen was not the graph the system actually used. Here the
// picture and the retriever read the same snapshot.
//
// On colour: it carries exactly one thing in each register — the relation on an
// edge, the node type on a node — and both are also carried by shape and named
// in the legend and the panel. Nothing is identified by hue alone.
// ---------------------------------------------------------------------------

export const NODE_TYPE_ORDER: GraphNodeType[] = [
  "document",
  "section",
  "chunk",
  "concept",
  "value",
  "constraint",
  "community",
  "facet",
];

export const RELATION_ORDER: GraphRelation[] = [
  "contains",
  "has_chunk",
  "follows",
  "mentions",
  "co_occurs",
  "in_community",
  "similar_to",
  "governed_by",
];

export const NODE_TYPE_LABEL: Record<GraphNodeType, string> = {
  document: "Document",
  section: "Section",
  chunk: "Chunk",
  concept: "Concept",
  value: "Value",
  constraint: "Constraint",
  community: "Community",
  facet: "Governance",
};

/** Short gloss under each filter chip — what the node *is*, not what it links to. */
export const NODE_TYPE_HINT: Record<GraphNodeType, string> = {
  document: "One converted source file",
  section: "One heading and its body",
  chunk: "A retrieval unit — what an answer cites",
  concept: "A term mined from the corpus",
  value: "A money amount or percentage",
  constraint: "A limit, waiting period or age bound",
  community: "A cluster of related concepts",
  facet: "Department, classification or access group",
};

/**
 * The corpus, as the system actually works through it.
 *
 * The graph holds eight node types and nothing on screen says which came
 * first, so the same snapshot can be read as a pile of coloured dots. These
 * are those types put back in the order the pipeline builds them: a file is
 * converted, split at its headings, cut into passages, mined for terms, and
 * the terms are clustered. Governance sits apart because it is recorded at
 * upload rather than derived from the text.
 *
 * Each stage doubles as a filter, so reading the pipeline and isolating a
 * layer of the graph are the same gesture.
 */
export type PipelineStage = {
  id: string;
  label: string;
  caption: string;
  types: GraphNodeType[];
};

export const PIPELINE: PipelineStage[] = [
  {
    id: "source",
    label: "Documents",
    caption: "Converted source files",
    types: ["document"],
  },
  {
    id: "outline",
    label: "Sections",
    caption: "Split at their headings",
    types: ["section"],
  },
  {
    id: "passages",
    label: "Chunks",
    caption: "The unit an answer cites",
    types: ["chunk"],
  },
  {
    id: "terms",
    label: "Terms",
    caption: "Concepts, amounts, limits",
    types: ["concept", "value", "constraint"],
  },
  {
    id: "themes",
    label: "Communities",
    caption: "Clustered by co-occurrence",
    types: ["community"],
  },
  {
    id: "governance",
    label: "Governance",
    caption: "Recorded at upload",
    types: ["facet"],
  },
];

export const RELATION_LABEL: Record<GraphRelation, string> = {
  contains: "contains",
  has_chunk: "has chunk",
  follows: "follows",
  mentions: "mentions",
  co_occurs: "co-occurs with",
  in_community: "in community",
  similar_to: "similar to",
  governed_by: "governed by",
};

/**
 * How each relation reads when the selected node is on the receiving end.
 *
 * Every edge is stored in one direction, but the panel is written from the
 * point of view of whatever is selected: a document *contains* a section, while
 * that section is a *section of* the document. Naming the inverse properly is
 * what keeps direction readable, since the canvas draws no arrowheads.
 */
export const RELATION_INVERSE_LABEL: Record<GraphRelation, string> = {
  contains: "section of",
  has_chunk: "chunk of",
  follows: "precedes",
  mentions: "mentioned in",
  co_occurs: "co-occurs with",
  in_community: "groups",
  similar_to: "similar to",
  governed_by: "governs",
};

export const RELATION_DESCRIPTION: Record<GraphRelation, string> = {
  contains: "The document's own outline: the sections it is made of.",
  has_chunk: "Which retrieval units came out of which section.",
  follows: "Reading order. Rejoins a table or clause split across two chunks.",
  mentions: "A concept, amount or constraint occurring in this passage.",
  co_occurs: "Two concepts associated more strongly than chance would predict (NPMI).",
  in_community: "The theme this concept was clustered into by label propagation.",
  similar_to: "Nearest passage by TF-IDF cosine — usually the same benefit in another document.",
  governed_by: "The department, classification or access group recorded at upload.",
};

/** Relations the retriever is allowed to walk, mirroring `RELATION_WEIGHT` in graph_rag.py. */
export const TRAVERSABLE: Record<GraphRelation, boolean> = {
  contains: false,
  has_chunk: true,
  follows: true,
  mentions: true,
  co_occurs: true,
  in_community: true,
  similar_to: true,
  governed_by: false,
};

// ---------------------------------------------------------------------------
// Palette
//
// Two registers, and they are deliberately not drawn from the same box.
//
// Nodes carry the brand: the pink the rest of the app is built on marks the
// document a passage came from, and the blue marks the passage itself. The
// other six are spaced around the wheel from those two, at a lightness that
// holds up against the near-black ground — the app's own #e6007e and #1857c4
// are far too dark to sit on it, so each is lifted rather than reused flat.
//
// Relations are lines one pixel wide, which is the harder problem: a hue that
// reads fine as a 12px solid disappears as a hairline. They are drawn brighter
// and further apart than the node hues, and the two relations that only carry
// file structure are kept deliberately grey so the semantic edges — the ones
// retrieval actually walks — are the ones that catch the eye.
//
// Neither register is load-bearing on its own. Node type is also a mesh shape,
// relation is also a named row in the key, and both are repeated in the panel.
// ---------------------------------------------------------------------------

export const NODE_COLOR: Record<GraphNodeType, string> = {
  document: "#ff3d8b",
  section: "#ff8a4c",
  chunk: "#35c2ff",
  concept: "#a98bff",
  value: "#3de0a0",
  constraint: "#ffc53d",
  community: "#e85cff",
  facet: "#8fa6c4",
};

/**
 * The colour a node's own light contributes.
 *
 * Each mesh is lit conventionally *and* emits a fraction of its own colour, so
 * a node reads as a lamp in the dark rather than as a painted ball. Landmarks
 * emit a little more than leaves, which is what makes the documents and the
 * community hubs findable from across the scene without labelling all five
 * hundred nodes.
 *
 * These are kept low deliberately. Emission is added on top of the lighting
 * and then again by the bloom pass, so a value that looks merely bright on one
 * node turns the middle of a dense cluster — where a hundred of them overlap —
 * into a single white smear.
 */
export const NODE_GLOW: Record<GraphNodeType, number> = {
  document: 0.28,
  section: 0.16,
  chunk: 0.2,
  concept: 0.18,
  value: 0.2,
  constraint: 0.2,
  community: 0.3,
  facet: 0.12,
};

export const RELATION_COLOR: Record<GraphRelation, string> = {
  contains: "#64789b",
  has_chunk: "#3d8fd1",
  follows: "#17b8c9",
  mentions: "#8b7bff",
  co_occurs: "#ff5c9a",
  in_community: "#d857f5",
  similar_to: "#35ce86",
  governed_by: "#c2914a",
};

/**
 * Rest length per relation for the 3D force engine.
 *
 * These shape the clusters. Structural edges are short so a document holds its
 * own sections and chunks tightly; the hub relations are long so the concept
 * and community regions push out into space of their own rather than
 * collapsing everything onto the origin.
 */
export const LINK_DISTANCE: Record<GraphRelation, number> = {
  contains: 34,
  has_chunk: 26,
  follows: 22,
  mentions: 70,
  co_occurs: 95,
  in_community: 130,
  similar_to: 55,
  governed_by: 150,
};

export type NetNode = KnowledgeNode & {
  /** Draw radius, in layout units. */
  radius: number;
  degree: number;
  /** Free text the search box matches against, pre-lowercased. */
  haystack: string;
};

export type Neighbour = {
  id: string;
  kind: GraphRelation;
  /** True when the neighbour is the target of the stored edge. */
  outgoing: boolean;
};

export type KnowledgeNetwork = {
  nodes: NetNode[];
  edges: KnowledgeEdge[];
  byId: Map<string, NetNode>;
  /** Adjacency, both directions, for the detail panel and hover dimming. */
  neighbours: Map<string, Neighbour[]>;
  neighbourIds: Map<string, Set<string>>;
  counts: Record<GraphRelation, number>;
  typeCounts: Record<GraphNodeType, number>;
  /** Documents, for the filter dropdown. */
  documents: { id: string; title: string }[];
};

/**
 * Draw radius.
 *
 * Sized by what makes a node worth finding rather than by degree alone. A
 * community stands for dozens of concepts and reads as a landmark; a chunk is
 * the unit an answer cites and stays legible even in a dense cluster; a value
 * or constraint is a leaf and stays small. Square-rooted throughout, so a node
 * ten times larger is not drawn ten times wider.
 */
function radiusFor(node: KnowledgeNode): number {
  switch (node.type) {
    case "community":
      return 9 + Math.sqrt(node.concept_count ?? 1) * 2.4;
    case "document":
      return 8 + Math.sqrt(node.chunk_count ?? 1) * 1.9;
    case "facet":
      return 6 + Math.sqrt(node.document_count ?? 1) * 1.7;
    case "section":
      return 4.6 + Math.sqrt(node.chunk_count ?? 1) * 0.9;
    case "chunk":
      return 5.2;
    case "concept":
      return 3.4 + Math.sqrt(node.chunk_frequency ?? 1) * 1.15;
    default:
      return 3.2;
  }
}

function haystackFor(node: KnowledgeNode): string {
  const parts = [
    node.label,
    node.title,
    node.meta,
    node.heading,
    node.document_title,
    node.normal_form,
    node.subtype,
    node.department,
    node.classification,
    // The passage text itself, so searching for a figure finds the chunk
    // carrying it and not merely the concept named after it.
    node.excerpt,
    node.members?.join(" "),
  ];
  return parts.filter(Boolean).join(" ").toLowerCase();
}

export function buildNetwork(graph: KnowledgeGraph): KnowledgeNetwork {
  const nodes: NetNode[] = graph.nodes.map((node) => ({
    ...node,
    radius: radiusFor(node),
    degree: 0,
    haystack: haystackFor(node),
  }));

  const byId = new Map(nodes.map((node) => [node.id, node]));

  const neighbours = new Map<string, Neighbour[]>();
  const neighbourIds = new Map<string, Set<string>>();
  const counts = Object.fromEntries(
    RELATION_ORDER.map((kind) => [kind, 0]),
  ) as Record<GraphRelation, number>;

  // A compiled snapshot should never name a node it did not declare, but a
  // stale file on disk can. Dropping such an edge keeps the picture honest
  // rather than inventing an endpoint for it.
  const edges = graph.edges.filter((edge) => byId.has(edge.source) && byId.has(edge.target));

  for (const edge of edges) {
    counts[edge.kind] += 1;

    const from = neighbours.get(edge.source) ?? [];
    from.push({ id: edge.target, kind: edge.kind, outgoing: true });
    neighbours.set(edge.source, from);

    const to = neighbours.get(edge.target) ?? [];
    to.push({ id: edge.source, kind: edge.kind, outgoing: false });
    neighbours.set(edge.target, to);

    const fromSet = neighbourIds.get(edge.source) ?? new Set<string>();
    fromSet.add(edge.target);
    neighbourIds.set(edge.source, fromSet);

    const toSet = neighbourIds.get(edge.target) ?? new Set<string>();
    toSet.add(edge.source);
    neighbourIds.set(edge.target, toSet);

    byId.get(edge.source)!.degree += 1;
    byId.get(edge.target)!.degree += 1;
  }

  const typeCounts = Object.fromEntries(
    NODE_TYPE_ORDER.map((type) => [type, 0]),
  ) as Record<GraphNodeType, number>;
  for (const node of nodes) typeCounts[node.type] += 1;

  return {
    nodes,
    edges,
    byId,
    neighbours,
    neighbourIds,
    counts,
    typeCounts,
    documents: graph.documents.map((doc) => ({ id: doc.document_id, title: doc.title })),
  };
}

/**
 * The document a node belongs to, or null for the corpus-wide nodes.
 *
 * Concepts, values, constraints and communities are deliberately cross-document
 * — a concept exists precisely because it appears in several places — so they
 * have no single owner and are never filtered out by a document filter. That is
 * the correct behaviour: hiding them would break every path between the
 * documents the filter left visible.
 */
export function documentOf(node: NetNode): string | null {
  if (node.type === "document") return node.id;
  if (node.type === "section" || node.type === "chunk") return node.document_id ?? null;
  return null;
}

/** Ids the search box currently matches. Fewer than two characters matches nothing. */
export function searchNetwork(net: KnowledgeNetwork, query: string, limit = 10): NetNode[] {
  const needle = query.trim().toLowerCase();
  if (needle.length < 2) return [];
  const hits: NetNode[] = [];
  for (const node of net.nodes) {
    if (node.haystack.includes(needle)) {
      hits.push(node);
      if (hits.length >= limit) break;
    }
  }
  return hits;
}
