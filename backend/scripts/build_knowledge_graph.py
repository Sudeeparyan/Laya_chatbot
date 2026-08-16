"""Compile the corpus into the knowledge-graph snapshot the app serves and walks.

    python backend/scripts/build_knowledge_graph.py

Reads ``data/local/markdown_outputs`` through the retriever's own index, runs
the extraction in ``app/services/graph_builder.py``, and writes
``data/knowledge_graph.json``.

The graph is compiled to a file rather than rebuilt per request for the same
reason the answer set is fixed: a snapshot is a citable artefact. The counts
quoted in the write-up, the screenshots of the graph and the retrieval traces
behind the evaluation all refer to one file with one fingerprint, and none of
them drift because a document was uploaded in between. The fingerprint of the
corpus is stored alongside the graph, so the app can say the snapshot is stale
rather than pretend otherwise.

Re-run this after ``build_mock_corpus.py``, and after any upload that should be
reflected in the graph.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import KNOWLEDGE_GRAPH_PATH, MARKDOWN_OUTPUT_DIR  # noqa: E402
from app.services.graph_builder import build_graph  # noqa: E402
from app.services.rag_service import build_index  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=KNOWLEDGE_GRAPH_PATH,
        help=f"Where to write the snapshot (default: {KNOWLEDGE_GRAPH_PATH}).",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=1,
        help="JSON indent. 1 keeps the file diffable without tripling its size.",
    )
    args = parser.parse_args()

    index = build_index(force=True)
    if index.is_empty:
        print(
            f"No Markdown found in {MARKDOWN_OUTPUT_DIR}.\n"
            "Run `python backend/scripts/build_mock_corpus.py` first, or convert a document "
            "on the Documents tab.",
            file=sys.stderr,
        )
        return 1

    print(f"Indexed {len(index.docs)} document(s), {len(index.chunks)} chunk(s). Compiling…")
    graph = build_graph(index)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(graph, indent=args.indent, ensure_ascii=False), encoding="utf-8"
    )

    stats = graph["stats"]
    size_kb = args.output.stat().st_size / 1024

    print(f"\nWrote {args.output}  ({size_kb:,.0f} KB, fingerprint {graph['corpus_fingerprint']})\n")
    print(f"  {stats['nodes']:>5} nodes    {stats['edges']:>5} edges    mean degree {stats['mean_degree']}")
    print("\n  Nodes by type")
    for node_type, count in graph["stats"]["nodes_by_type"].items():
        print(f"    {node_type:<12} {count:>5}")
    print("\n  Edges by relation")
    for kind, count in graph["stats"]["edges_by_kind"].items():
        print(f"    {kind:<14} {count:>5}")
    print(
        f"\n  {stats['communities']} concept communities, "
        f"{stats['cross_document_similar_edges']} cross-document similarity edges, "
        f"{stats['isolated_nodes']} isolated nodes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
