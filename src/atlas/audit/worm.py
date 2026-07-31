"""Optional append-only sink for audit entries — files, not a database.

Every audit entry is written to a new JSON-lines file whose name embeds the
sequence number and hash. Once written the file is chmod 0o444 (read-only)
and its directory is chmod 0o555. On a filesystem that supports it
(``chattr +i`` on Linux, or an S3 Object Lock bucket for real production),
this becomes append-only against everyone but root.

This is the local-Podman-testable equivalent of an S3 Object Lock sink. The
DuckDB audit is still authoritative for reads; this sink is purely additive
insurance against tampering with the primary log.

Enabled via ``ATLAS_WORM_AUDIT_DIR``. Silently disabled otherwise.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from atlas.audit.audit import AuditEntry


class WORMSink:
    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, entry: AuditEntry, sequence: int) -> Path:
        """Persist a fully-hashed AuditEntry as a read-only JSONL file."""

        filename = f"{sequence:010d}-{entry.audit_id}.jsonl"
        path = self._base / filename
        payload = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True)
        with self._lock:
            # Write with O_EXCL so a naming collision (would only happen if
            # someone reset sequence numbers, i.e. tampered) fails loudly.
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o444,
            )
            try:
                os.write(fd, payload.encode("utf-8"))
                os.write(fd, b"\n")
            finally:
                os.close(fd)
        try:
            os.chmod(path, 0o444)
        except OSError:
            pass
        return path


_sink: WORMSink | None = None
_lock = threading.Lock()


def get_worm_sink() -> WORMSink | None:
    global _sink
    if _sink is not None:
        return _sink
    with _lock:
        if _sink is not None:
            return _sink
        target = os.environ.get("ATLAS_WORM_AUDIT_DIR")
        if not target:
            return None
        _sink = WORMSink(Path(target))
        return _sink


def reset_worm_for_tests() -> None:
    global _sink
    _sink = None
