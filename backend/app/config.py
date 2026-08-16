import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_UPLOAD_DIR = PROJECT_ROOT / "data" / "local" / "raw_uploads"
MARKDOWN_OUTPUT_DIR = PROJECT_ROOT / "data" / "local" / "markdown_outputs"
MANIFEST_DIR = PROJECT_ROOT / "data" / "local" / "manifests"
FEEDBACK_LOG = PROJECT_ROOT / "data" / "local" / "chat_feedback.jsonl"
# The compiled knowledge graph. It backs both the graph view and the
# graph-expansion retriever, so a stale snapshot changes retrieval as well as
# the picture. Rebuilt by backend/scripts/build_knowledge_graph.py.
KNOWLEDGE_GRAPH_PATH = PROJECT_ROOT / "data" / "knowledge_graph.json"
# MarkItDown normally comes from `pip install -r requirements.txt`. If a checked-out
# copy of its source happens to sit here, it is put on sys.path first so a local
# fork can be developed against without reinstalling. Absent by default.
MARKITDOWN_SOURCE_DIR = PROJECT_ROOT / "example_code" / "packages" / "markitdown" / "src"
MAX_UPLOAD_BYTES = 75 * 1024 * 1024

# Azure OpenAI
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_CHAT_DEPLOYMENT = os.getenv("AZURE_CHAT_DEPLOYMENT", "gpt-4.1")
AZURE_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")

# TLS verification for Azure OpenAI calls. Certificates must be verified — the
# request carries the API key — so this defaults to on. Set
# AZURE_OPENAI_VERIFY_SSL=false only as a temporary workaround on a machine
# behind a TLS-inspecting corporate proxy, and never in a deployed environment.
AZURE_OPENAI_VERIFY_SSL = os.getenv("AZURE_OPENAI_VERIFY_SSL", "true").strip().lower() not in (
    "false",
    "0",
    "no",
)


def ensure_data_dirs() -> None:
    for path in (RAW_UPLOAD_DIR, MARKDOWN_OUTPUT_DIR, MANIFEST_DIR):
        path.mkdir(parents=True, exist_ok=True)