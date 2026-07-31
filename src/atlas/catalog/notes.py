"""Tiny, dependency-free loader for human-authored semantic catalog notes.

Notes are read verbatim into LLM prompts, so anyone with write access to the
notes directory controls part of the prompt. When
`ATLAS_ENFORCE_SEMANTIC_INTEGRITY` is set, the loader requires a manifest file
(default `docs/semantic/manifest.json`) listing `{ "path": "<sha256>" }` for
every Markdown file. Files with mismatched hashes are refused.

Regenerate the manifest with:

    python -m atlas.catalog.notes --regenerate docs/semantic
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from atlas.config.settings import get_settings
from atlas.observability.logging import get_logger


_TABLE_HEADER = re.compile(r"^##\s+table:\s*([A-Za-z_][\w]*\.[A-Za-z_][\w]*)\s*$", re.IGNORECASE)


class SemanticIntegrityError(RuntimeError):
    """Raised when integrity is enforced and a note fails its hash check."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest(manifest_path: Path) -> dict[str, str]:
    if not manifest_path.exists():
        return {}
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _verify_file(rel: str, data: bytes, manifest: dict[str, str], enforce: bool) -> bool:
    expected = manifest.get(rel)
    digest = _sha256(data)
    if expected is None:
        if enforce:
            raise SemanticIntegrityError(
                f"Semantic note '{rel}' has no entry in the integrity manifest."
            )
        return True
    if expected != digest:
        if enforce:
            raise SemanticIntegrityError(
                f"Semantic note '{rel}' hash mismatch (expected {expected}, got {digest})."
            )
        get_logger("atlas.semantic").warning(
            "semantic_note_hash_mismatch path=%s expected=%s actual=%s", rel, expected, digest
        )
        return False
    return True


def load_notes(dir: str = "docs/semantic") -> dict[str, str]:
    """Load ``## table: schema.table`` sections from Markdown files in *dir*."""

    notes_dir = Path(dir)
    if not notes_dir.exists():
        return {}

    settings = get_settings()
    enforce = settings.enforce_semantic_integrity
    manifest_path = settings.semantic_manifest_path or (notes_dir / "manifest.json")
    manifest = _load_manifest(manifest_path)

    notes: dict[str, str] = {}
    for path in sorted(notes_dir.glob("*.md")):
        data = path.read_bytes()
        rel = path.name
        if not _verify_file(rel, data, manifest, enforce):
            continue

        current_key: str | None = None
        body: list[str] = []

        def store() -> None:
            if current_key is not None:
                notes[current_key] = "\n".join(body).strip()

        for line in data.decode("utf-8").splitlines():
            match = _TABLE_HEADER.match(line)
            if match:
                store()
                current_key = match.group(1).lower()
                body = []
            elif current_key is not None:
                body.append(line)
        store()
    return notes


def regenerate_manifest(dir: str = "docs/semantic") -> Path:
    """Rewrite the integrity manifest for the given directory."""

    notes_dir = Path(dir)
    manifest_path = notes_dir / "manifest.json"
    manifest: dict[str, str] = {}
    for path in sorted(notes_dir.glob("*.md")):
        manifest[path.name] = _sha256(path.read_bytes())
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--regenerate":
        target = regenerate_manifest(sys.argv[2])
        print(f"Wrote manifest: {target}")
    else:
        print("Usage: python -m atlas.catalog.notes --regenerate <dir>")
        sys.exit(2)
