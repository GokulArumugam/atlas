"""Tiny, dependency-free loader for human-authored semantic catalog notes."""

from __future__ import annotations

from pathlib import Path
import re


_TABLE_HEADER = re.compile(r"^##\s+table:\s*([A-Za-z_][\w]*\.[A-Za-z_][\w]*)\s*$", re.IGNORECASE)


def load_notes(dir: str = "docs/semantic") -> dict[str, str]:
    """Load ``## table: schema.table`` sections from Markdown files in *dir*.

    The body is intentionally kept as readable Markdown/plain text.  This gives the
    later context builder human-approved meaning without bringing in a Markdown or
    YAML dependency.
    """
    notes_dir = Path(dir)
    if not notes_dir.exists():
        return {}

    notes: dict[str, str] = {}
    for path in sorted(notes_dir.glob("*.md")):
        current_key: str | None = None
        body: list[str] = []

        def store() -> None:
            if current_key is not None:
                notes[current_key] = "\n".join(body).strip()

        for line in path.read_text(encoding="utf-8").splitlines():
            match = _TABLE_HEADER.match(line)
            if match:
                store()
                current_key = match.group(1).lower()
                body = []
            elif current_key is not None:
                body.append(line)
        store()
    return notes
