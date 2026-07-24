"""The schema catalog — read-only, and always scoped through policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from atlas.catalog.notes import load_notes
from atlas.policy.engine import PolicyEngine
from atlas.policy.model import ColumnRef, TableRef


@dataclass(frozen=True)
class ColumnInfo:
    schema: str
    table: str
    column: str
    data_type: str
    is_pii: bool


@dataclass
class TableInfo:
    ref: TableRef
    columns: list[ColumnInfo]
    row_count: int
    description: str = ""


class Catalog:
    """A snapshot of real warehouse metadata; data connections are always read-only."""

    def __init__(self, db_path: str, policy: PolicyEngine) -> None:
        self.db_path = str(db_path)
        self.policy = policy
        self.notes = load_notes()
        self._tables = self._introspect()
        self._by_ref = {table.ref: table for table in self._tables}

    def all_tables(self) -> list[TableInfo]:
        """Return every user table found in the warehouse, in stable order."""
        return list(self._tables)

    def tables_for(self, user: str) -> list[TableInfo]:
        """Return only policy-visible tables; unknown users receive an empty map."""
        visible = self.policy.visible_tables(user)
        return [table for table in self._tables if table.ref in visible]

    def column_stats(self, column: ColumnRef, sample: int = 5) -> dict:
        """Return small generation aids without ever reading values from a PII column."""
        if self.policy.is_pii(column):
            return {"masked": True}
        if column not in {ColumnRef(info.schema, info.table, info.column)
                          for table in self._tables for info in table.columns}:
            raise ValueError("Column is not present in the introspected catalog.")

        sample = max(0, int(sample))
        table_name = _qualified(column.schema, column.table)
        column_name = _quote(column.column)
        connection = duckdb.connect(self.db_path, read_only=True)
        try:
            samples = connection.execute(
                f"SELECT DISTINCT {column_name} FROM {table_name} "
                f"WHERE {column_name} IS NOT NULL ORDER BY {column_name} LIMIT ?",
                [sample],
            ).fetchall()
            minimum, maximum = connection.execute(
                f"SELECT MIN({column_name}), MAX({column_name}) FROM {table_name}"
            ).fetchone()
        finally:
            connection.close()
        return {
            "masked": False,
            "samples": [row[0] for row in samples],
            "min": minimum,
            "max": maximum,
        }

    def _introspect(self) -> list[TableInfo]:
        connection = duckdb.connect(self.db_path, read_only=True)
        try:
            rows = connection.execute(
                """
                SELECT table_schema, table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY table_schema, table_name, ordinal_position
                """
            ).fetchall()
            table_names = connection.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY table_schema, table_name
                """
            ).fetchall()
            counts = {
                TableRef(schema, table): connection.execute(
                    f"SELECT COUNT(*) FROM {_qualified(schema, table)}"
                ).fetchone()[0]
                for schema, table in table_names
            }
        finally:
            connection.close()

        columns_by_table: dict[TableRef, list[ColumnInfo]] = {ref: [] for ref in counts}
        for schema, table, column, data_type in rows:
            ref = TableRef(schema, table)
            if ref not in columns_by_table:
                continue
            column_ref = ColumnRef(schema, table, column)
            columns_by_table[ref].append(
                ColumnInfo(schema, table, column, data_type, self.policy.is_pii(column_ref))
            )
        return [
            TableInfo(ref, columns_by_table[ref], counts[ref], self.notes.get(str(ref), ""))
            for ref in sorted(counts, key=lambda item: (item.schema, item.table))
        ]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _qualified(schema: str, table: str) -> str:
    return f"{_quote(schema)}.{_quote(table)}"
