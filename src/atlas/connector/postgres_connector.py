"""PostgreSQL connector — connect Atlas to any Postgres-compatible warehouse."""

from __future__ import annotations

from typing import Any

from atlas.connector.base import ForeignKeyInfo, WarehouseConnector

_FK_QUERY = """
SELECT
    tc.table_schema,
    tc.table_name,
    kcu.column_name,
    ccu.table_schema  AS ref_schema,
    ccu.table_name    AS ref_table,
    ccu.column_name   AS ref_column
FROM information_schema.table_constraints  AS tc
JOIN information_schema.key_column_usage   AS kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema   = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema NOT IN ('information_schema', 'pg_catalog')
"""


class PostgresConnector(WarehouseConnector):
    """Postgres connector backed by psycopg2.

    Pass a DSN string, e.g. 'postgresql://user:pass@host:5432/db'.
    The connection is kept open for the lifetime of the connector; call
    close() or use it as a context manager.

    Requires psycopg2:
        pip install "atlas-analyst[postgres]"
    """

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg2
        except ImportError as exc:
            raise ImportError(
                "psycopg2 is required for the Postgres connector. "
                'Install it with: pip install "atlas-analyst[postgres]"'
            ) from exc
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        self._conn = conn

    @property
    def dialect(self) -> str:
        return "postgres"

    def execute(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params or None)
            columns = [desc[0] for desc in cursor.description]
            return columns, cursor.fetchall()

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        try:
            _, rows = self.execute(_FK_QUERY)
        except Exception:
            return []
        return [
            ForeignKeyInfo(schema, table, column, ref_schema, ref_table, ref_column)
            for schema, table, column, ref_schema, ref_table, ref_column in rows
        ]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
