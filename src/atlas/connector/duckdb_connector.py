"""DuckDB connector — the default backend for local Atlas deployments."""

from __future__ import annotations

import re
from typing import Any

import duckdb

from atlas.connector.base import ForeignKeyInfo, WarehouseConnector


class DuckDBConnector(WarehouseConnector):
    """Opens a fresh read-only DuckDB connection per execute call.

    DuckDB's single-file model makes per-call connections cheap and avoids
    concurrent-write lock conflicts with the audit database.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = str(db_path)

    @property
    def dialect(self) -> str:
        return "duckdb"

    def execute(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        conn = duckdb.connect(self._db_path, read_only=True)
        try:
            cursor = conn.execute(sql, params or [])
            columns = [desc[0] for desc in cursor.description]
            return columns, cursor.fetchall()
        finally:
            conn.close()

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

        # DuckDB exposes FK metadata as a constraint_text string; parse the
        # REFERENCES clause to extract the referenced table and column.
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
