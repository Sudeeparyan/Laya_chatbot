import type { ForceGraph3DInstance } from "3d-force-graph";
import { Maximize2, Minus, Orbit, Plus, RotateCcw, Sparkles, Tag } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

import type { GraphNodeType, GraphRelation } from "../api";
import {
  LINK_DISTANCE,
  NODE_COLOR,
  NODE_GLOW,
  RELATION_COLOR,
  RELATION_LABEL,
  RELATION_ORDER,
  documentOf,
  type KnowledgeNetwork,
  type NetNode,
} from "./knowledgeGraphModel";

// ---------------------------------------------------------------------------
// The 3D canvas
//
// A WebGL scene you orbit with the mouse: drag to rotate, right-drag to pan,
// wheel to dolly. Three dimensions are not decoration here — the same graph
// drawn flat is a hairball, because ~2,200 relations over ~510 nodes have
// nowhere to go on a plane and every cluster lands on top of every other.
//
// What makes the third dimension readable is not the projection but the depth
// cues: nodes emit their own light so they read as lamps rather than discs,
// distance fog drops the far side of the graph back, and a still starfield
// behind everything gives the parallax that tells you the camera moved. Without
// those the scene is a flat picture of a 3D thing.
//
// Node type is carried by mesh shape as well as colour, and relation by edge
// colour, with both named in the legend and repeated in the detail panel. So
// nothing in the picture depends on telling two hues apart.
// ---------------------------------------------------------------------------

/** The library mutates the data it is given, adding simulation state. */
type SimNode = NetNode & { x?: number; y?: number; z?: number };
type SimEdge = { source: string | SimNode; target: string | SimNode; kind: GraphRelation };
type Graph3D = ForceGraph3DInstance<SimNode, SimEdge>;

/**
 * The package's default export is declared with the base node and link types
 * rather than as a generic constructor, so it is re-typed here to the shapes
 * this scene actually holds. That keeps every accessor below type-checked
 * against `SimNode` / `SimEdge` instead of falling back to `any`.
 */
type ForceGraph3DConstructor = new (element: HTMLElement) => Graph3D;

/**
 * The ground the scene sits on, and the colour the distance fog fades to.
 *
 * Pure black, and it has to be. The frame is drawn into the composer's buffer
 * and encoded again by the output pass on its way to the canvas, and the clear
 * colour picks up that encoding without having been written for it — so a
 * near-black ground of #060911 arrived on screen around rgb(42,50,61), a flat
 * slate the dimmed nodes and links then had to compete with. Zero is the one
 * value that comes through that round trip unchanged.
 */
const CANVAS_BG = "#000000";
const DIM_NODE = "#2a3348";
const DIM_EDGE = "#161d2c";
/** The picked node. Not pure white — see `FOCUS_GLOW`. */
const FOCUS_COLOUR = "#dce7f7";
/** Retrieval trace: the colour a highlighted node and its edges take. */
const TRACE_COLOUR = "#e8bf3f";
/** The app's pink, lifted to sit on the near-black ground. */
const BRAND_PINK = "#ff3d8b";

/**
 * Emission on the node under the cursor and on a traced node.
 *
 * Held down near the ordinary node values on purpose. A focused node used to
 * be pure white at high emission, which bloomed into a headlight that lit half
 * the scene — it is meant to be the one thing you are looking at, not the one
 * thing you cannot look away from.
 */
const FOCUS_GLOW = 0.34;

/** Idle this long after the layout settles and the render loop is paused: a
    full-screen WebGL canvas redrawing ~2,700 objects at 60fps otherwise heats
    the machine for as long as the tab is open. */
const IDLE_PAUSE_MS = 2200;

/**
 * The longest the loop is allowed to keep running purely to animate.
 *
 * Flow beads need the renderer awake, but "a node is selected" is a state that
 * can last for hours — someone reads the panel and walks away — and a canvas
 * that renders forever because of a click that happened at nine in the morning
 * is exactly how a machine ends up with a hot fan and a sluggish browser. Any
 * interaction resets this; when it runs out the loop parks itself, and the next
 * pointer move brings it straight back.
 */
const ANIMATION_BUDGET_MS = 20000;

/** Breathing room, in px, left around the graph when the view is fitted. */
const FIT_PADDING = 45;

/** Types that get a floating name. Small populations only — see `makeLabel`. */
const LABELLED_TYPES = new Set<GraphNodeType>(["document", "community", "facet"]);

export function nodeColor(node: NetNode): string {
  return NODE_COLOR[node.type] ?? "#94a3b8";
}

/**
 * Repulsion per node type — landmarks push harder so their regions stay apart.
 *
 * Communities and documents are the things you navigate by, so they are given
 * room; leaves are packed tightly around whatever they belong to.
 */
function chargeFor(node: NetNode): number {
  switch (node.type) {
    case "community":
      return -520;
    case "document":
      return -420;
    case "facet":
      return -300;
    case "concept":
      return -150;
    case "section":
      return -110;
    case "chunk":
      return -95;
    default:
      return -55;
  }
}

// ---------------------------------------------------------------------------
// Meshes
//
// Each node type gets its own solid, so type survives being read by someone
// who cannot separate the eight hues. Geometries and materials are built once
// and shared across every node that needs them: ~510 meshes over 8 geometries
// and a couple of dozen materials, rather than 510 of each.
// ---------------------------------------------------------------------------

const geometryCache = new Map<GraphNodeType, THREE.BufferGeometry>();

function geometryFor(type: GraphNodeType): THREE.BufferGeometry {
  const cached = geometryCache.get(type);
  if (cached) return cached;

  // Low segment counts on purpose — at the sizes these draw, a smoother
  // sphere costs vertices nobody can see.
  let geometry: THREE.BufferGeometry;
  switch (type) {
    case "document":
      // A slab, so a source file reads as a page among the round things.
      geometry = new THREE.BoxGeometry(1.7, 2.2, 0.35);
      break;
    case "section":
      geometry = new THREE.BoxGeometry(1.5, 1.0, 1.0);
      break;
    case "chunk":
      geometry = new THREE.SphereGeometry(1, 16, 12);
      break;
    case "concept":
      geometry = new THREE.OctahedronGeometry(1.3);
      break;
    case "value":
      geometry = new THREE.TetrahedronGeometry(1.45);
      break;
    case "constraint":
      geometry = new THREE.ConeGeometry(1.2, 2.2, 6);
      break;
    case "community":
      geometry = new THREE.IcosahedronGeometry(1.25);
      break;
    default:
      geometry = new THREE.TorusGeometry(1, 0.36, 8, 18);
      break;
  }
  geometryCache.set(type, geometry);
  return geometry;
}

const materialCache = new Map<string, THREE.MeshLambertMaterial>();

/**
 * A shared material.
 *
 * `glow` is the fraction of its own colour the surface emits. It is what keeps
 * a node legible on the far side of the graph, where the lights barely reach
 * and distance fog has already taken most of its lit colour away.
 */
function materialFor(color: string, opacity = 1, glow = 0): THREE.MeshLambertMaterial {
  const key = `${color}@${opacity}@${glow}`;
  const cached = materialCache.get(key);
  if (cached) return cached;
  const base = new THREE.Color(color);
  const material = new THREE.MeshLambertMaterial({
    color: base,
    emissive: base.clone().multiplyScalar(glow),
    transparent: opacity < 1,
    opacity,
  });
  materialCache.set(key, material);
  return material;
}

/** How much bigger than its data radius each solid is drawn. */
const MESH_SCALE = 1.05;

/**
 * A node's floating name.
 *
 * Only the landmark types get one — there are a few dozen of those against
 * ~450 chunks and concepts, and a name on every node is a wall of text that
 * hides the graph it is describing. Drawn to a canvas and mapped onto a sprite,
 * so it always faces the camera however the scene is turned.
 */
function buildLabel(text: string, color: string): THREE.Sprite {
  const clipped = text.length > 30 ? `${text.slice(0, 29)}…` : text;
  const font = '600 44px "Segoe UI", system-ui, sans-serif';
  const padX = 20;
  const height = 74;

  const canvas = document.createElement("canvas");
  const measure = canvas.getContext("2d")!;
  measure.font = font;
  const width = Math.ceil(measure.measureText(clipped).width) + padX * 2;

  // Sizing the canvas resets every context property, so the font is set again
  // below rather than being carried over from the measuring pass.
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d")!;
  context.font = font;
  context.fillStyle = "rgba(6, 9, 17, 0.74)";
  context.beginPath();
  context.roundRect(0, 0, width, height, 16);
  context.fill();
  context.fillStyle = color;
  context.textBaseline = "middle";
  context.fillText(clipped, padX, height / 2 + 2);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }),
  );
  // Canvas pixels to layout units. Tuned against the whole graph in frame,
  // which is the hardest case: any smaller and the names are unreadable specks
  // at the only zoom level where you actually need them to navigate.
  const unitsPerPixel = 0.24;
  sprite.scale.set(width * unitsPerPixel, height * unitsPerPixel, 1);
  return sprite;
}

/** Kept per node, so toggling names off and on does not re-render the text. */
const labelCache = new Map<string, THREE.Sprite>();

function labelFor(node: NetNode): THREE.Sprite {
  const cached = labelCache.get(node.id);
  if (cached) return cached;
  const sprite = buildLabel(node.title, nodeColor(node));
  labelCache.set(node.id, sprite);
  return sprite;
}

/**
 * What the engine hangs at a node's position.
 *
 * Landmarks come back as a group — the solid plus its name — so `__threeObj`
 * is not always the mesh. Both parts are stashed on the group's `userData` so
 * repainting focus never has to walk the tree to find them again.
 */
function buildNodeObject(node: NetNode, withLabels: boolean): THREE.Object3D {
  const mesh = new THREE.Mesh(
    geometryFor(node.type),
    materialFor(nodeColor(node), 1, NODE_GLOW[node.type] ?? 0.35),
  );
  const scale = Math.max(node.radius * MESH_SCALE, 3);
  mesh.scale.setScalar(scale);
  mesh.userData.baseScale = scale;

  if (!withLabels || !LABELLED_TYPES.has(node.type)) return mesh;

  const label = labelFor(node);
  label.position.set(0, scale + label.scale.y * 0.9, 0);
  const group = new THREE.Group();
  group.add(mesh);
  group.add(label);
  group.userData.mesh = mesh;
  group.userData.label = label;
  return group;
}

/** The mesh inside whatever the engine is holding for a node. */
function meshOf(object: THREE.Object3D | undefined): THREE.Mesh | null {
  if (!object) return null;
  if ((object as THREE.Mesh).isMesh) return object as THREE.Mesh;
  return (object.userData?.mesh as THREE.Mesh | undefined) ?? null;
}

/** The far backdrop: still points, no motion, purely for parallax. */
function makeStarfield(): THREE.Points {
  const count = 1400;
  const positions = new Float32Array(count * 3);
  for (let index = 0; index < count; index += 1) {
    // On a shell rather than through the volume, so nothing ever ends up
    // sitting among the nodes pretending to be one.
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const radius = 2600 + Math.random() * 2200;
    positions[index * 3] = radius * Math.sin(phi) * Math.cos(theta);
    positions[index * 3 + 1] = radius * Math.sin(phi) * Math.sin(theta);
    positions[index * 3 + 2] = radius * Math.cos(phi);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  return new THREE.Points(
    geometry,
    new THREE.PointsMaterial({
      color: "#8fa6c4",
      size: 5.5,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.5,
      // Stars are the one thing in the scene that must not fade with distance:
      // they are the distance.
      fog: false,
      depthWrite: false,
    }),
  );
}

// ---------------------------------------------------------------------------
// Legend marks
//
// The panel needs a flat 2D swatch per node type. These mirror the 3D meshes:
// a sphere reads as a disc, a box as a rectangle, an octahedron as a diamond.
// ---------------------------------------------------------------------------

export function NodeMark({
  node,
  color,
  scale = 1,
}: {
  node: NetNode;
  color: string;
  scale?: number;
}) {
  const r = node.radius * scale;
  const stroke = "#0b1220";

  if (node.type === "chunk") {
    return <circle r={r} fill={color} stroke={stroke} strokeWidth={1.2} />;
  }
  if (node.type === "document") {
    const w = r * 1.35;
    const h = r * 1.85;
    return <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={1} fill={color} stroke={stroke} strokeWidth={1.1} />;
  }
  if (node.type === "section") {
    const w = r * 2.0;
    const h = r * 1.25;
    return <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={h / 3} fill={color} stroke={stroke} strokeWidth={1.1} />;
  }
  if (node.type === "concept") {
    const s = r * 1.05;
    return <rect x={-s} y={-s} width={s * 2} height={s * 2} fill={color} transform="rotate(45)" />;
  }
  if (node.type === "value") {
    const s = r * 1.4;
    return <polygon points={`0,${-s} ${s * 0.87},${s * 0.5} ${-s * 0.87},${s * 0.5}`} fill={color} />;
  }
  if (node.type === "constraint") {
    const s = r * 1.4;
    return <polygon points={`0,${s} ${s * 0.87},${-s * 0.5} ${-s * 0.87},${-s * 0.5}`} fill={color} />;
  }
  if (node.type === "community") {
    // Hexagon — the icosahedron's flat cousin.
    const s = r * 1.2;
    const points = Array.from({ length: 6 }, (_, index) => {
      const angle = (Math.PI / 3) * index - Math.PI / 2;
      return `${(s * Math.cos(angle)).toFixed(2)},${(s * Math.sin(angle)).toFixed(2)}`;
    }).join(" ");
    return <polygon points={points} fill={color} />;
  }
  return <circle r={r} fill="none" stroke={color} strokeWidth={Math.max(1.6, r * 0.35)} />;
}

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

type GraphCanvasProps = {
  net: KnowledgeNetwork;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  hiddenTypes: Set<GraphNodeType>;
  hiddenRelations: Set<GraphRelation>;
  activeDocument: string | null;
  matchedIds: Set<string> | null;
  /** Nodes on a replayed retrieval trace. Null when no answer is being replayed. */
  traceNodes: Set<string> | null;
  onVisibleChange?: (counts: { nodes: number; edges: number }) => void;
  /** Bumping this re-frames the camera. */
  resetSignal: number;
  /** Focus this node — the camera flies to it. */
  flyToId: string | null;
};

function GraphCanvas({
  net,
  selectedId,
  onSelect,
  hiddenTypes,
  hiddenRelations,
  activeDocument,
  matchedIds,
  traceNodes,
  onVisibleChange,
  resetSignal,
  flyToId,
}: GraphCanvasProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<Graph3D | null>(null);
  const hoverIdRef = useRef<string | null>(null);

  /** Slow automatic orbit — the cheapest way to read depth off a still scene. */
  const [orbiting, setOrbiting] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  /**
   * Bloom, on a switch, and off to begin with.
   *
   * It looks like a per-node halo at ten nodes. At four hundred, all those
   * halos overlap and what it actually does is lift the entire frame off black
   * — the background included — which is the opposite of what a dark scene is
   * for. Left in because it is a real look and some screens carry it well, but
   * you have to ask for it, and it is also the most expensive thing on the
   * frame, so the default is both darker and cheaper.
   */
  const [glow, setGlow] = useState(false);
  const bloomRef = useRef<{ enabled: boolean } | null>(null);
  const glowRef = useRef(glow);
  glowRef.current = glow;

  // Everything the imperative graph callbacks need, kept in refs so the
  // accessors never close over a stale render.
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const matchedRef = useRef(matchedIds);
  matchedRef.current = matchedIds;
  const traceRef = useRef(traceNodes);
  traceRef.current = traceNodes;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const labelsRef = useRef(showLabels);
  labelsRef.current = showLabels;

  // ── Visibility ──────────────────────────────────────────────────────────
  // Document-owned nodes filter directly. The corpus-wide nodes — concepts,
  // values, constraints, communities, facets — survive as long as something
  // they touch is still on screen, so filtering to one document never strands
  // a concept in empty space or cuts the paths between what is left.
  const visibleIds = useMemo(() => {
    const primary = new Set<string>();
    for (const node of net.nodes) {
      if (hiddenTypes.has(node.type)) continue;
      const owner = documentOf(node);
      if (owner === null) continue;
      if (activeDocument && owner !== activeDocument) continue;
      primary.add(node.id);
    }

    const visible = new Set(primary);
    for (const node of net.nodes) {
      if (primary.has(node.id) || hiddenTypes.has(node.type)) continue;
      if (documentOf(node) !== null) continue;
      for (const id of net.neighbourIds.get(node.id) ?? []) {
        if (primary.has(id)) {
          visible.add(node.id);
          break;
        }
      }
    }
    return visible;
  }, [net, hiddenTypes, activeDocument]);

  const visibleRef = useRef(visibleIds);
  visibleRef.current = visibleIds;

  const hiddenRelationsRef = useRef(hiddenRelations);
  hiddenRelationsRef.current = hiddenRelations;

  const visibleEdgeCount = useMemo(
    () =>
      net.edges.filter(
        (edge) =>
          !hiddenRelations.has(edge.kind) &&
          visibleIds.has(edge.source) &&
          visibleIds.has(edge.target),
      ).length,
    [net, hiddenRelations, visibleIds],
  );

  useEffect(() => {
    onVisibleChange?.({ nodes: visibleIds.size, edges: visibleEdgeCount });
  }, [visibleIds, visibleEdgeCount, onVisibleChange]);

  // ── Render-loop throttling ──────────────────────────────────────────────
  const pausedRef = useRef(false);
  const idleTimerRef = useRef<number | null>(null);
  const lastTickRef = useRef(0);

  /**
   * Something on screen is animating and would look frozen if the loop parked.
   *
   * Pausing the render loop is what keeps an idle full-screen WebGL canvas
   * from heating the machine, but it stops *everything*, and beads halfway
   * along an edge stopping dead reads as a hang rather than a saving. So while
   * a node is picked or a walk is being replayed the loop keeps running — but
   * only for `ANIMATION_BUDGET_MS` past the last thing the user actually did.
   */
  const animatingRef = useRef(false);
  animatingRef.current = Boolean(selectedId) || Boolean(traceNodes?.size);
  const lastActionRef = useRef(Date.now());

  const wake = useCallback((hold = IDLE_PAUSE_MS) => {
    const graph = graphRef.current;
    if (!graph) return;
    if (pausedRef.current) {
      graph.resumeAnimation();
      pausedRef.current = false;
    }
    if (idleTimerRef.current) window.clearTimeout(idleTimerRef.current);
    idleTimerRef.current = window.setTimeout(() => {
      if (!graphRef.current || pausedRef.current) return;
      // Still simulating — a node drag re-heats it — so check back rather
      // than freezing the layout mid-flight.
      if (Date.now() - lastTickRef.current < 800) {
        wake(800);
        return;
      }
      if (animatingRef.current && Date.now() - lastActionRef.current < ANIMATION_BUDGET_MS) {
        wake(1600);
        return;
      }
      graphRef.current.pauseAnimation();
      pausedRef.current = true;
    }, hold);
  }, []);

  /** Wake, and restart the animation budget. For anything the user drives. */
  const act = useCallback(
    (hold?: number) => {
      lastActionRef.current = Date.now();
      wake(hold);
    },
    [wake],
  );

  // A selection change is an action: it both starts the beads and, when it
  // clears, is the moment the loop is allowed to idle again — and nothing else
  // calls `wake` at that point to start the clock.
  useEffect(() => {
    act();
  }, [selectedId, traceNodes, act]);

  /**
   * Paint hover, selection and trace emphasis.
   *
   * The meshes already exist, so this swaps their shared materials rather than
   * rebuilding anything. Precedence runs narrowest-first: an explicit hover or
   * selection wins over a replayed retrieval trace, which wins over a search.
   * Only one question is ever being answered on screen at a time.
   */
  const paintFocus = useCallback(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const focus = hoverIdRef.current ?? selectedRef.current;
    const matched = matchedRef.current;
    const trace = traceRef.current;
    const near = focus ? net.neighbourIds.get(focus) : undefined;

    for (const node of graph.graphData().nodes as (SimNode & { __threeObj?: THREE.Object3D })[]) {
      const holder = node.__threeObj;
      const mesh = meshOf(holder);
      if (!mesh) continue;

      let color: string;
      if (focus) {
        if (node.id === focus) color = FOCUS_COLOUR;
        else color = near?.has(node.id) ? nodeColor(node) : DIM_NODE;
      } else if (trace) {
        color = trace.has(node.id) ? TRACE_COLOUR : DIM_NODE;
      } else if (matched) {
        color = matched.has(node.id) ? nodeColor(node) : DIM_NODE;
      } else {
        color = nodeColor(node);
      }

      // A replayed trace is a needle-in-haystack read, so everything off the
      // walk drops further than it does for a hover or a search — those are
      // narrowing an already-focused view, this one is picking a dozen nodes
      // out of five hundred.
      const dimmed = color === DIM_NODE;
      const floor = trace && !focus ? 0.1 : 0.26;
      const lit = node.id === focus || (!dimmed && Boolean(trace?.has(node.id)));
      mesh.material = materialFor(
        color,
        dimmed ? floor : 1,
        dimmed ? 0 : lit ? FOCUS_GLOW : NODE_GLOW[node.type] ?? 0.2,
      );

      // The picked node also grows, because a colour swap alone is easy to
      // lose in a dense cluster.
      const base = (mesh.userData.baseScale as number | undefined) ?? 1;
      mesh.scale.setScalar(node.id === focus ? base * 1.45 : base);

      const label = holder?.userData?.label as THREE.Sprite | undefined;
      if (label) {
        const material = label.material as THREE.SpriteMaterial;
        material.opacity = dimmed ? 0.18 : 1;
      }
    }

    // Links go through the colour accessor, which is cheap for GL lines.
    graph.linkColor(graph.linkColor());
    wake();
  }, [net, wake]);

  /**
   * Re-run the particle accessor.
   *
   * Deliberately not part of `paintFocus`: re-applying it builds a fresh set of
   * bead meshes for every link, which is fine on a click and visibly stutters
   * if it happens on every hover.
   */
  const paintFlow = useCallback(() => {
    const graph = graphRef.current;
    if (!graph) return;
    graph
      .linkDirectionalParticles(graph.linkDirectionalParticles())
      .linkDirectionalParticleColor(graph.linkDirectionalParticleColor());
    wake();
  }, [wake]);

  // ── Set-up ──────────────────────────────────────────────────────────────
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;

    // The whole three.js bundle is pulled in on demand, so opening the rest of
    // the app never pays for it.
    import("3d-force-graph").then((module) => {
      if (disposed || !hostRef.current) return;
      const ForceGraph3D = module.default as unknown as ForceGraph3DConstructor;

      const focusOf = () => hoverIdRef.current ?? selectedRef.current;
      const idOf = (endpoint: string | SimNode) =>
        typeof endpoint === "string" ? endpoint : endpoint.id;

      /** Edges worth animating: the walk being replayed, or the picked node's own. */
      const isFlowing = (edge: SimEdge) => {
        const source = idOf(edge.source);
        const target = idOf(edge.target);
        const trace = traceRef.current;
        if (trace) return trace.has(source) && trace.has(target);
        const picked = selectedRef.current;
        return Boolean(picked) && (source === picked || target === picked);
      };

      const graph: Graph3D = new ForceGraph3D(hostRef.current)
        .backgroundColor(CANVAS_BG)
        .width(hostRef.current.clientWidth)
        .height(hostRef.current.clientHeight)
        .showNavInfo(false)
        .nodeVisibility((node: SimNode) => visibleRef.current.has(node.id))
        .nodeThreeObject((node: SimNode) => buildNodeObject(node, labelsRef.current))
        .nodeLabel((node: SimNode) => {
          const context = node.document_title && node.type !== "document" ? node.document_title : "";
          return `<div class="net-tooltip is-open">
               <strong><span class="net-tooltip-dot" style="background:${nodeColor(node)}"></span>${escapeHtml(node.title)}</strong>
               <p>${escapeHtml(node.meta)}</p>
               <span class="net-tooltip-meta">${escapeHtml(context ? `${context} · ` : "")}${node.degree} relations</span>
             </div>`;
        })
        // linkWidth stays 0 so the library draws fast GL lines. Any non-zero
        // width builds one cylinder mesh per link, which at ~2,200 links drops
        // the scene to single-digit frame rates.
        .linkVisibility(
          (edge: SimEdge) =>
            !hiddenRelationsRef.current.has(edge.kind) &&
            visibleRef.current.has(idOf(edge.source)) &&
            visibleRef.current.has(idOf(edge.target)),
        )
        .linkColor((edge: SimEdge) => {
          const focus = focusOf();
          const source = idOf(edge.source);
          const target = idOf(edge.target);
          if (focus) {
            return source === focus || target === focus ? RELATION_COLOR[edge.kind] : DIM_EDGE;
          }
          const trace = traceRef.current;
          if (trace) {
            return trace.has(source) && trace.has(target) ? TRACE_COLOUR : DIM_EDGE;
          }
          return RELATION_COLOR[edge.kind];
        })
        // A thousand semi-transparent lines converging on the middle of the
        // frame add up: at anything higher the centre of the graph turns into a
        // solid pale mass with the nodes lost inside it.
        .linkOpacity(0.2)
        // Beads travelling along an edge, in the direction the relation is
        // stored. On a replayed answer this is the retrieval walk itself: you
        // watch relevance leave the seed passage and arrive at the ones the
        // graph added.
        .linkDirectionalParticles((edge: SimEdge) => (isFlowing(edge) ? 3 : 0))
        .linkDirectionalParticleSpeed(0.006)
        .linkDirectionalParticleWidth(2.2)
        .linkDirectionalParticleResolution(6)
        .linkDirectionalParticleColor((edge: SimEdge) =>
          traceRef.current ? TRACE_COLOUR : RELATION_COLOR[edge.kind],
        )
        .warmupTicks(70)
        .d3AlphaMin(0.02)
        .onEngineTick(() => {
          lastTickRef.current = Date.now();
        })
        .onNodeClick((node: SimNode) => {
          onSelectRef.current(selectedRef.current === node.id ? null : node.id);
        })
        .onNodeHover((node: SimNode | null) => {
          const id = node ? node.id : null;
          if (id === hoverIdRef.current) return;
          hoverIdRef.current = id;
          if (hostRef.current) hostRef.current.style.cursor = node ? "pointer" : "grab";
          paintFocus();
        })
        .onBackgroundClick(() => onSelectRef.current(null))
        .graphData({
          nodes: net.nodes as SimNode[],
          // The engine rewrites source/target into node references, so it gets
          // copies; `net.edges` keeps its plain ids for every lookup elsewhere.
          links: net.edges.map((edge) => ({
            source: edge.source,
            target: edge.target,
            kind: edge.kind,
          })),
        });

      // Both forces are installed by the engine's default configuration, but
      // the accessor is typed as possibly absent, so neither call is assumed.
      graph.d3Force("charge")?.strength((node: SimNode) => chargeFor(node));
      graph.d3Force("link")?.distance((edge: SimEdge) => LINK_DISTANCE[edge.kind] ?? 80);

      // ── Depth ────────────────────────────────────────────────────────────
      // Lighting is deliberately not neutral: a cool key from above and a pink
      // fill from below-behind, which is the app's own accent doing the work of
      // separating the near face of a solid from its far one.
      //
      // Kept dim. Five hundred small solids packed into the middle of the frame
      // sum their contributions, so lighting that looks correct on one node
      // blows the centre of the cluster out to white.
      graph.lights([
        new THREE.AmbientLight(0x8ea3c4, 0.7),
        (() => {
          const key = new THREE.DirectionalLight(0xffffff, 1.1);
          key.position.set(1, 1.2, 1);
          return key;
        })(),
        (() => {
          const rim = new THREE.DirectionalLight(BRAND_PINK, 0.45);
          rim.position.set(-1, -0.7, -0.9);
          return rim;
        })(),
      ]);

      const scene = graph.scene();
      const stars = makeStarfield();
      scene.add(stars);

      // At 2x this canvas is fill-rate bound for no visible gain on a scene of
      // flat lines and small solids.
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
      graph.renderer().setPixelRatio(pixelRatio);

      // Bloom. Everything already emits some of its own colour, and this is
      // what turns that into light in the air around it. Guarded because it is
      // the one part of the scene that can fail on a weak GL implementation,
      // and a graph without glow is still a graph.
      Promise.all([
        import("three/examples/jsm/postprocessing/UnrealBloomPass.js"),
        import("three/examples/jsm/postprocessing/OutputPass.js"),
      ])
        .then(([{ UnrealBloomPass }, { OutputPass }]) => {
          if (disposed || graphRef.current !== graph) return;
          const composer = graph.postProcessingComposer();
          composer.setPixelRatio(pixelRatio);
          const size = new THREE.Vector2(graph.width(), graph.height());
          // Weak, tight and high-threshold, in that order of importance. A wide
          // radius here is what turns four hundred small halos into one flat
          // wash across the whole canvas, so it is kept close to the source.
          const bloom = new UnrealBloomPass(size, 0.3, 0.28, 0.66);
          bloom.enabled = glowRef.current;
          composer.addPass(bloom);
          // Without a final output pass the composer hands back linear colour
          // and the whole scene washes out.
          composer.addPass(new OutputPass());
          bloomRef.current = bloom;
          wake();
        })
        .catch(() => {
          /* no glow on this machine — the scene is unaffected otherwise */
        });

      // The engine reports a stop while the graph is still contracting, so a
      // fit taken at that moment frames something larger than what settles a
      // moment later and leaves it small in the window. Fitting again once it
      // has actually stopped moving is what frames it properly.
      //
      // When a retrieval trace is being replayed, both fits target the traced
      // nodes instead of the whole graph. Otherwise this fires after the trace
      // effect below has already closed in, pulls the camera straight back out
      // to frame all five hundred nodes, and the walk you came to look at ends
      // up as a dozen specks in the middle.
      /**
       * Distance fog, sized to the graph rather than to a fixed number.
       *
       * This is the depth cue that does the most work — without it the far
       * side of the cloud is drawn exactly as brightly as the near side and
       * the scene reads as a flat spray of dots. It cannot be a constant,
       * though: the engine picks its own scale from the link distances and the
       * node count, and a density that gives a graph of 500 nodes a pleasant
       * haze erases a graph twice that size completely. So it is derived from
       * how far the nodes actually ended up spreading, once they have stopped
       * moving, and aimed at roughly a third of the ground colour mixed in at
       * the back of the cloud with the whole thing in frame.
       */
      const applyFog = () => {
        let spread = 0;
        for (const node of net.nodes as SimNode[]) {
          if (!Number.isFinite(node.x)) continue;
          spread = Math.max(spread, Math.hypot(node.x!, node.y!, node.z!));
        }
        if (spread < 1) return;
        scene.fog = new THREE.FogExp2(CANVAS_BG, 0.24 / spread);
      };

      let framed = false;
      const fit = (ms: number, extra = 0) => {
        const trace = traceRef.current;
        const scoped = trace && trace.size > 0;
        graphRef.current?.zoomToFit(
          ms,
          FIT_PADDING + extra,
          scoped ? (node: SimNode) => trace.has(node.id) : undefined,
        );
      };
      // Three fits, not one. The engine reports a stop the moment its alpha
      // falls under the floor, which is well before the graph has finished
      // settling, and a fit taken then frames a cloud that is still moving —
      // sometimes leaving it a small knot in the middle of an empty canvas.
      // Each later fit costs nothing and corrects the one before it.
      graph.onEngineStop(() => {
        if (framed) return;
        framed = true;
        applyFog();
        fit(700);
        window.setTimeout(() => fit(600), 1400);
        window.setTimeout(() => {
          applyFog();
          fit(500);
        }, 3200);
        // Held open past the last fit so the camera tween cannot be frozen
        // half way through it.
        wake(5000);
      });

      graphRef.current = graph;
      wake(6000);
    });

    return () => {
      disposed = true;
      if (idleTimerRef.current) window.clearTimeout(idleTimerRef.current);
      const graph = graphRef.current;
      if (graph) {
        graph._destructor();
        graphRef.current = null;
      }
    };
  }, [net, paintFocus, wake]);

  // Keep the canvas sized to its container.
  useEffect(() => {
    function onResize() {
      const graph = graphRef.current;
      const host = hostRef.current;
      if (!graph || !host) return;
      graph.width(host.clientWidth).height(host.clientHeight);
      wake();
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [wake]);

  // Filters, selection and trace only ever flip visibility and colour. Feeding
  // the library new graphData instead would re-heat the simulation from alpha 1
  // and send every node flying on each chip toggle.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.nodeVisibility(graph.nodeVisibility()).linkVisibility(graph.linkVisibility());
    paintFocus();
  }, [visibleIds, hiddenRelations, matchedIds, traceNodes, selectedId, paintFocus]);

  // Flow beads follow the selection and the replayed walk only.
  useEffect(() => {
    paintFlow();
  }, [selectedId, traceNodes, visibleIds, hiddenRelations, paintFlow]);

  // Names on the landmarks. Toggling rebuilds every node object, so the focus
  // paint has to be reapplied on top of the fresh materials.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    graph.nodeThreeObject(graph.nodeThreeObject());
    paintFocus();
  }, [showLabels, paintFocus]);

  // The pass stays in the chain either way; disabling it is a flag, not a
  // rebuild, so the switch is instant.
  useEffect(() => {
    if (bloomRef.current) bloomRef.current.enabled = glow;
    wake();
  }, [glow, wake]);

  // Escape backs out of a selection without having to find empty space to
  // click on, which in a dense graph there may not be any of.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && selectedRef.current) onSelectRef.current(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Slow orbit. The camera is walked around the scene's vertical axis at a
  // couple of degrees a second — fast enough that the depth reads, slow enough
  // to keep looking at. Any drag or wheel turns it off, because at that point
  // the user is driving.
  useEffect(() => {
    if (!orbiting) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timer = window.setInterval(() => {
      const graph = graphRef.current;
      if (!graph) return;
      const { x, y, z } = graph.cameraPosition();
      const angle = 0.0035;
      graph.cameraPosition({
        x: x * Math.cos(angle) - z * Math.sin(angle),
        y,
        z: x * Math.sin(angle) + z * Math.cos(angle),
      });
      wake(1200);
    }, 33);
    return () => window.clearInterval(timer);
  }, [orbiting, wake]);

  // Fly the camera to a node picked from the search list or the panel.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !flyToId) return;
    const node = net.byId.get(flyToId) as SimNode | undefined;
    // Positions only exist once the engine has run a tick; before that there is
    // nowhere to fly to.
    if (!node || !Number.isFinite(node.x) || !Number.isFinite(node.y) || !Number.isFinite(node.z)) {
      return;
    }
    const target = { x: node.x as number, y: node.y as number, z: node.z as number };

    // Stand off along the vector from the origin, so the camera ends up outside
    // the graph looking in rather than buried inside a cluster.
    //
    // The distance is measured against the graph's own radius rather than fixed:
    // the engine picks its own scale from the link distances and node count, and
    // a constant that frames one graph nicely puts the camera inside the next
    // one, with the nearest solids filling the screen.
    let spread = 0;
    for (const other of net.nodes as SimNode[]) {
      if (!Number.isFinite(other.x)) continue;
      spread = Math.max(spread, Math.hypot(other.x!, other.y!, other.z!));
    }
    // A leaf's neighbourhood is small; a community or document hub can have
    // dozens of spokes reaching much further and needs more room.
    const isLeaf = node.type === "chunk" || node.type === "value" || node.type === "constraint";
    const distance = isLeaf ? Math.max(spread * 0.5, 380) : Math.max(spread * 0.8, 560);
    const ratio = 1 + distance / (Math.hypot(target.x, target.y, target.z) || 1);
    graph.cameraPosition(
      { x: target.x * ratio, y: target.y * ratio, z: target.z * ratio },
      target,
      900,
    );
    wake(2600);
  }, [flyToId, net, wake]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || resetSignal === 0) return;
    graph.zoomToFit(700, FIT_PADDING);
    wake(2400);
  }, [resetSignal, wake]);

  // Frame a replayed retrieval trace.
  //
  // Highlighting the walk is not much use if it lands as a dozen small marks in
  // the middle of five hundred others, so the camera closes in on just those
  // nodes. Deferred because the trace usually arrives while the layout is still
  // settling, and a fit taken then frames positions that are about to move.
  useEffect(() => {
    if (!traceNodes || traceNodes.size === 0) return;
    const timer = window.setTimeout(() => {
      const graph = graphRef.current;
      if (!graph) return;
      graph.zoomToFit(900, FIT_PADDING * 2, (node: SimNode) => traceNodes.has(node.id));
      wake(3000);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [traceNodes, wake]);

  function dolly(factor: number) {
    const graph = graphRef.current;
    if (!graph) return;
    const camera = graph.cameraPosition();
    graph.cameraPosition(
      { x: camera.x * factor, y: camera.y * factor, z: camera.z * factor },
      undefined,
      320,
    );
    act();
  }

  /** Hand control back to the user the moment they touch the scene. */
  const takeOver = useCallback(() => {
    setOrbiting(false);
    act();
  }, [act]);

  return (
    <div className="net-canvas-wrap">
      <div
        ref={hostRef}
        className="net-canvas"
        onPointerDown={takeOver}
        onPointerMove={() => act()}
        onWheel={takeOver}
      />

      <div className="net-tools">
        <button
          type="button"
          className={`net-tool${orbiting ? " is-on" : ""}`}
          onClick={() => setOrbiting((on) => !on)}
          aria-pressed={orbiting}
          title="Turn the scene slowly, so depth reads without dragging"
        >
          <Orbit size={15} />
          Orbit
        </button>
        <button
          type="button"
          className={`net-tool${showLabels ? " is-on" : ""}`}
          onClick={() => setShowLabels((on) => !on)}
          aria-pressed={showLabels}
          title="Name the documents, communities and governance nodes in the scene"
        >
          <Tag size={15} />
          Labels
        </button>
        <button
          type="button"
          className={`net-tool${glow ? " is-on" : ""}`}
          onClick={() => setGlow((on) => !on)}
          aria-pressed={glow}
          title="Adds light in the air around the brightest nodes. Lifts the whole scene off black, and costs a pass on every frame"
        >
          <Sparkles size={15} />
          Glow
        </button>
      </div>

      <div className="net-zoom">
        <button type="button" onClick={() => dolly(0.78)} aria-label="Zoom in">
          <Plus size={15} />
        </button>
        <button type="button" onClick={() => dolly(1.28)} aria-label="Zoom out">
          <Minus size={15} />
        </button>
        <button
          type="button"
          onClick={() => {
            graphRef.current?.zoomToFit(700, FIT_PADDING);
            act(2400);
          }}
          aria-label="Fit the whole graph in view"
        >
          <RotateCcw size={15} />
        </button>
      </div>

      <div className="net-relation-key">
        {RELATION_ORDER.map((kind) => (
          <span
            key={kind}
            className={`net-relation-item${hiddenRelations.has(kind) ? " is-off" : ""}`}
          >
            <span className="net-relation-swatch" style={{ background: RELATION_COLOR[kind] }} />
            {RELATION_LABEL[kind]}
            <span className="net-relation-count">{net.counts[kind]}</span>
          </span>
        ))}
      </div>

      <p className="net-hint">
        <Maximize2 size={13} /> Drag to rotate · right-drag to pan · scroll to zoom · click a node to
        trace its relations · Esc to clear
      </p>
    </div>
  );
}

export default GraphCanvas;
