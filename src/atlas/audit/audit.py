"""An append-only audit log with a SHA-256 hash chain you can verify.

Efficiency notes:
* One persistent DuckDB connection is kept for writes; reads use per-call
  cursors. This eliminates the fresh file-open cost that used to happen on
  every /api/ask.
* `touching_schema` now filters in SQL instead of loading every row into
  Python.
* `verify_chain` streams rows in batches — memory-flat regardless of size.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import duckdb


_GENESIS_HASH = "0" * 64
_TABLE = "audit_entries"


@dataclass
class AuditEntry:
    audit_id: str
    ts: str
    user: str
    team: str
    question: str
    generated_sql: str | None
    executed_sql: str | None
    decision: str
    reason: str
    tables_touched: list[str]
    columns_touched: list[str]
    masked_columns: list[str]
    row_count: int | None
    latency_ms: dict
    prev_hash: str
    entry_hash: str


class AuditLog:
    """Persist audit entries and detect any subsequent modification."""

    def __init__(self, db_path: str = "data/audit.duckdb") -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()
        self._initialize()

    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(self.db_path)
        return self._conn

    def _initialize(self) -> None:
        with self._lock:
            self._connection().execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    sequence BIGINT PRIMARY KEY,
                    audit_id VARCHAR NOT NULL,
                    ts VARCHAR NOT NULL,
                    username VARCHAR NOT NULL,
                    team VARCHAR NOT NULL,
                    question VARCHAR NOT NULL,
                    generated_sql VARCHAR,
                    executed_sql VARCHAR,
                    decision VARCHAR NOT NULL,
                    reason VARCHAR NOT NULL,
                    tables_touched VARCHAR NOT NULL,
                    columns_touched VARCHAR NOT NULL,
                    masked_columns VARCHAR NOT NULL,
                    row_count BIGINT,
                    latency_ms VARCHAR NOT NULL,
                    prev_hash VARCHAR NOT NULL,
                    entry_hash VARCHAR NOT NULL
                )
                """
            )

    def record(self, **fields: Any) -> AuditEntry:
        """Append one entry and return its fully hashed representation."""

        payload = self._normalise_fields(fields)
        with self._lock:
            conn = self._connection()
            previous = conn.execute(
                f"SELECT sequence, entry_hash FROM {_TABLE} ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = (int(previous[0]) + 1) if previous else 1
            prev_hash = str(previous[1]) if previous else _GENESIS_HASH
            entry_hash = _hash(prev_hash, payload)
            entry = AuditEntry(prev_hash=prev_hash, entry_hash=entry_hash, **payload)
            conn.execute(
                f"""
                INSERT INTO {_TABLE} (
                    sequence, audit_id, ts, username, team, question, generated_sql,
                    executed_sql, decision, reason, tables_touched, columns_touched,
                    masked_columns, row_count, latency_ms, prev_hash, entry_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    sequence, entry.audit_id, entry.ts, entry.user, entry.team,
                    entry.question, entry.generated_sql, entry.executed_sql,
                    entry.decision, entry.reason, _canonical_json(entry.tables_touched),
                    _canonical_json(entry.columns_touched), _canonical_json(entry.masked_columns),
                    entry.row_count, _canonical_json(entry.latency_ms), entry.prev_hash,
                    entry.entry_hash,
                ],
            )
            return entry

    def all(self, limit: int = 100) -> list[AuditEntry]:
        limit = max(0, int(limit))
        if limit == 0:
            return []
        return self._read_entries(
            f"SELECT {self._select_columns()} FROM {_TABLE} ORDER BY sequence DESC LIMIT ?", [limit]
        )

    def for_user(self, user: str) -> list[AuditEntry]:
        return self._read_entries(
            f"SELECT {self._select_columns()} FROM {_TABLE} WHERE username = ? ORDER BY sequence DESC",
            [user],
        )

    def touching_schema(self, schema: str) -> list[AuditEntry]:
        """Return entries whose lineage touches *schema* — filter pushed to SQL."""

        pattern = f"{schema.lower()}.%"
        return self._read_entries(
            f"""
            SELECT {self._select_columns()}
            FROM {_TABLE}
            WHERE lower(tables_touched) LIKE ? OR lower(columns_touched) LIKE ?
            ORDER BY sequence DESC
            """,
            [f'%"{pattern}%', f'%"{pattern}%'],
        )

    def verify_chain(self, batch_size: int = 500) -> tuple[bool, str]:
        """Recompute every link — batched, so memory is flat regardless of size."""

        expected_prev = _GENESIS_HASH
        total = 0
        with self._lock:
            for entry in self._stream_entries(batch_size):
                total += 1
                if entry.prev_hash != expected_prev:
                    return False, (
                        f"Audit chain tampering detected at entry {entry.audit_id}: "
                        "previous hash mismatch."
                    )
                calculated = _hash(entry.prev_hash, self._payload_from_entry(entry))
                if entry.entry_hash != calculated:
                    return False, (
                        f"Audit chain tampering detected at entry {entry.audit_id}: "
                        "entry hash mismatch."
                    )
                expected_prev = entry.entry_hash
        return True, f"Audit chain verified ({total} entries)."

    def _stream_entries(self, batch_size: int) -> Iterator[AuditEntry]:
        conn = self._connection()
        offset = 0
        while True:
            rows = conn.execute(
                f"SELECT {self._select_columns()} FROM {_TABLE} "
                "ORDER BY sequence ASC LIMIT ? OFFSET ?",
                [batch_size, offset],
            ).fetchall()
            if not rows:
                return
            for row in rows:
                yield self._entry_from_row(row)
            offset += len(rows)

    @staticmethod
    def _select_columns() -> str:
        return (
            "audit_id, ts, username, team, question, generated_sql, executed_sql, decision, reason, "
            "tables_touched, columns_touched, masked_columns, row_count, latency_ms, prev_hash, entry_hash"
        )

    def _read_entries(self, sql: str, params: list[Any]) -> list[AuditEntry]:
        with self._lock:
            rows = self._connection().execute(sql, params).fetchall()
        return [self._entry_from_row(row) for row in rows]

    @staticmethod
    def _entry_from_row(row: tuple[Any, ...]) -> AuditEntry:
        return AuditEntry(
            audit_id=row[0], ts=row[1], user=row[2], team=row[3], question=row[4],
            generated_sql=row[5], executed_sql=row[6], decision=row[7], reason=row[8],
            tables_touched=json.loads(row[9]), columns_touched=json.loads(row[10]),
            masked_columns=json.loads(row[11]), row_count=row[12],
            latency_ms=json.loads(row[13]), prev_hash=row[14], entry_hash=row[15],
        )

    @staticmethod
    def _normalise_fields(fields: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "audit_id", "ts", "user", "team", "question", "generated_sql", "executed_sql",
            "decision", "reason", "tables_touched", "columns_touched", "masked_columns",
            "row_count", "latency_ms",
        }
        unexpected = set(fields) - allowed
        if unexpected:
            raise TypeError(f"Unexpected audit fields: {', '.join(sorted(unexpected))}")
        decision = fields.get("decision", "deny")
        if hasattr(decision, "value"):
            decision = decision.value
        return {
            "audit_id": str(fields.get("audit_id") or uuid4()),
            "ts": str(fields.get("ts") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
            "user": str(fields.get("user", "unknown")),
            "team": str(fields.get("team", "unknown")),
            "question": str(fields.get("question", "")),
            "generated_sql": fields.get("generated_sql"),
            "executed_sql": fields.get("executed_sql"),
            "decision": str(decision).lower(),
            "reason": str(fields.get("reason", "")),
            "tables_touched": _string_list(fields.get("tables_touched", [])),
            "columns_touched": _string_list(fields.get("columns_touched", [])),
            "masked_columns": _string_list(fields.get("masked_columns", [])),
            "row_count": None if fields.get("row_count") is None else int(fields["row_count"]),
            "latency_ms": dict(fields.get("latency_ms", {})),
        }

    @staticmethod
    def _payload_from_entry(entry: AuditEntry) -> dict[str, Any]:
        return {
            "audit_id": entry.audit_id, "ts": entry.ts, "user": entry.user, "team": entry.team,
            "question": entry.question, "generated_sql": entry.generated_sql,
            "executed_sql": entry.executed_sql, "decision": entry.decision, "reason": entry.reason,
            "tables_touched": entry.tables_touched, "columns_touched": entry.columns_touched,
            "masked_columns": entry.masked_columns, "row_count": entry.row_count,
            "latency_ms": entry.latency_ms,
        }

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


def _string_list(values: Any) -> list[str]:
    return [str(value) for value in values]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical_json(payload)).encode("utf-8")).hexdigest()
