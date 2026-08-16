"""Shared test fixtures.

``convert_upload`` writes the raw copy, the Markdown and the manifest to the
directories configured in ``app.config``. Left alone, running the suite would
drop test documents into the real ``data/local/`` folders — and, because the
chat index is built from whatever is in ``markdown_outputs``, into the
knowledge base as well. The autouse fixture below redirects those writes into
each test's own tmp directory.
"""
from pathlib import Path

import pytest

from app.services import markdown_converter


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every conversion output at a throwaway directory."""
    raw_dir = tmp_path / "raw_uploads"
    markdown_dir = tmp_path / "markdown_outputs"
    manifest_dir = tmp_path / "manifests"
    for directory in (raw_dir, markdown_dir, manifest_dir):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(markdown_converter, "RAW_UPLOAD_DIR", raw_dir)
    monkeypatch.setattr(markdown_converter, "MARKDOWN_OUTPUT_DIR", markdown_dir)
    monkeypatch.setattr(markdown_converter, "MANIFEST_DIR", manifest_dir)
    # Otherwise this would still create (and write to) the real folders.
    monkeypatch.setattr(markdown_converter, "ensure_data_dirs", lambda: None)
    return tmp_path
