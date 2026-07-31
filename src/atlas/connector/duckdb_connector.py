"""DuckDB connector — the default backend for local Atlas deployments.

DuckDB supports one *writer* per file but many concurrent *readers*. We keep a
single read-only connection open for the process lifetime and use `.cursor()`
to get per-thread cursors — DuckDB's Python driver documents that cursors on a
shared connection are safe to use across threads.

If the DuckDB file is missing (fresh clone) we surface a clear error rather
than silently opening an empty warehouse.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import duckdb

from atlas.connector.base import ForeignKeyInfo, WarehouseConnector


class DuckDBConnector(WarehouseConnector):
    """Long-lived read-only DuckDB connection, per-thread cursors."""

    def __init__(self, db_path: str) -> None:
        self._db_path = str(db_path)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._open_lock = threading.Lock()

    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self._conn is not None:
            return self._conn
        with self._open_lock:
            if self._conn is None:
                if not Path(self._db_path).exists():
                    raise FileNotFoundError(
                        f"DuckDB warehouse not found at {self._db_path}. Run atlas-generate."
                    )
                self._conn = duckdb.connect(self._db_path, read_only=True)
            return self._conn

    @property
    def dialect(self) -> str:
        return "duckdb"

    def execute(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        cursor = self._connection().cursor()
        try:
            cursor.execute(sql, params or [])
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall() if cursor.description else []
            return columns, rows
        finally:
            cursor.close()

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        try:
            _, rows = self.execute(
                """
                SELECT schema_name, table_name, constraint_text, constraint_column_names
                FROM duckdb_constraints()
                WHERE constraint_type = 'FOREIGN KEY'
                """
            )
        except Exception:
            return []

        pattern = re.compile(
            r"REFERENCES\s+(?:(?P<schema>[A-Za-z_]\w*)\.)?(?P<table>[A-Za-z_]\w*)\s*\((?P<column>[A-Za-z_]\w*)\)",
            re.IGNORECASE,
        )
        results: list[ForeignKeyInfo] = []
        for schema, table, constraint_text, columns in rows:
            match = pattern.search(constraint_text)
            if not match or not columns or len(columns) != 1:
                continue
            results.append(ForeignKeyInfo(
                schema=schema,
                table=table,
                column=columns[0],
                ref_schema=match.group("schema") or schema,
                ref_table=match.group("table"),
                ref_column=match.group("column"),
            ))
        return results

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
