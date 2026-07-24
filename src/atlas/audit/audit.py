"""An append-only audit log with a SHA-256 hash chain you can verify.

The audit database is kept separate from the warehouse on purpose — the agent
only ever *reads* the warehouse, and only ever *writes* here. The hash chain
means if someone quietly edits or deletes a row later, a verifier will notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
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
    """Persist audit entries and detect any subsequent modification.

    This is append-only by application convention.  The chain makes a direct
    database update or deletion evident to a verifier, even though DuckDB does
    not itself provide an immutable-table primitive.
    """

    def __init__(self, db_path: str = "data/audit.duckdb") -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def record(self, **fields: Any) -> AuditEntry:
        """Append one entry and return its fully hashed representation."""
        payload = self._normalise_fields(fields)
        connection = duckdb.connect(self.db_path)
        try:
            previous = connection.execute(
                f"SELECT sequence, entry_hash FROM {_TABLE} ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = (int(previous[0]) + 1) if previous else 1
            prev_hash = str(previous[1]) if previous else _GENESIS_HASH
            entry_hash = _hash(prev_hash, payload)
            entry = AuditEntry(prev_hash=prev_hash, entry_hash=entry_hash, **payload)
            connection.execute(
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
        finally:
            connection.close()

    def all(self, limit: int = 100) -> list[AuditEntry]:
        """Return newest entries first, bounded to a safe positive limit."""
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
        """Return entries whose resolved table or column lineage touches *schema*."""
        prefix = f"{schema.lower()}."
        return [
            entry for entry in self.all(limit=1_000_000)
            if any(item.lower().startswith(prefix) for item in entry.tables_touched + entry.columns_touched)
        ]

    def verify_chain(self) -> tuple[bool, str]:
        """Recompute every link and report the first evidence of tampering."""
        connection = duckdb.connect(self.db_path, read_only=True)
        try:
            rows = connection.execute(
                f"SELECT {self._select_columns()} FROM {_TABLE} ORDER BY sequence ASC"
            ).fetchall()
        finally:
            connection.close()

        expected_prev = _GENESIS_HASH
        for index, row in enumerate(rows, start=1):
            entry = self._entry_from_row(row)
            if entry.prev_hash != expected_prev:
                return False, f"Audit chain tampering detected at entry {entry.audit_id}: previous hash mismatch."
            calculated = _hash(entry.prev_hash, self._payload_from_entry(entry))
            if entry.entry_hash != calculated:
                return False, f"Audit chain tampering detected at entry {entry.audit_id}: entry hash mismatch."
            expected_prev = entry.entry_hash
        return True, f"Audit chain verified ({len(rows)} entries)."

    def _initialize(self) -> None:
        connection = duckdb.connect(self.db_path)
        try:
            connection.execute(
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
        finally:
            connection.close()

    @staticmethod
    def _select_columns() -> str:
        return (
            "audit_id, ts, username, team, question, generated_sql, executed_sql, decision, reason, "
            "tables_touched, columns_touched, masked_columns, row_count, latency_ms, prev_hash, entry_hash"
        )

    def _read_entries(self, sql: str, params: list[Any]) -> list[AuditEntry]:
        connection = duckdb.connect(self.db_path, read_only=True)
        try:
            rows = connection.execute(sql, params).fetchall()
        finally:
            connection.close()
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


def _string_list(values: Any) -> list[str]:
    return [str(value) for value in values]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical_json(payload)).encode("utf-8")).hexdigest()
