"""PostgreSQL connector — connect Atlas to any Postgres-compatible warehouse.

Two hardenings on top of the demo baseline:

1. Every session forces `SET SESSION default_transaction_read_only = on` and
   `SET SESSION statement_timeout` so that even if the SQL firewall ever misses,
   the DB refuses to write and refuses to run runaway queries.
2. A connection pool replaces the single shared connection so concurrent
   FastAPI workers don't share cursors.

The connector prefers `psycopg` (v3) with its built-in `ConnectionPool`, and
falls back to `psycopg2` with a lightweight lock-based pool if only v2 is
installed.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

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


_READ_ONLY_SETUP = (
    "SET SESSION default_transaction_read_only = on;"
    "SET SESSION statement_timeout = %(timeout_ms)s;"
    "SET SESSION idle_in_transaction_session_timeout = 60000;"
)


class PostgresConnector(WarehouseConnector):
    """Postgres connector with pooling and read-only session enforcement.

    Pass a DSN string, e.g. 'postgresql://user:pass@host:5432/db'.

    Recommended: point Atlas at a DB role that only has SELECT on the desired
    schemas. The session-level `default_transaction_read_only` is defense in
    depth; the DB role is the primary control.

    Requires psycopg (v3) or psycopg2:
        pip install "atlas-analyst[postgres]"

    :param dsn: Postgres connection string.
    :param min_size: Minimum pool size.
    :param max_size: Maximum pool size.
    :param statement_timeout_ms: Per-statement timeout in ms. Defaults to 30s.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 8,
        statement_timeout_ms: int = 30_000,
    ) -> None:
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms
        self._impl = self._build_pool(dsn, min_size, max_size)

    def _build_pool(self, dsn: str, min_size: int, max_size: int) -> "_PoolAdapter":
        try:
            import psycopg  # type: ignore
            from psycopg_pool import ConnectionPool  # type: ignore
        except ImportError:
            psycopg = None
            ConnectionPool = None

        if psycopg is not None and ConnectionPool is not None:
            pool = ConnectionPool(
                conninfo=dsn,
                min_size=min_size,
                max_size=max_size,
                kwargs={"autocommit": True},
                open=True,
            )
            return _Psycopg3PoolAdapter(pool, self._statement_timeout_ms)

        try:
            import psycopg2  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "psycopg (v3) or psycopg2 is required for the Postgres connector. "
                'Install it with: pip install "atlas-analyst[postgres]"'
            ) from exc
        return _Psycopg2PoolAdapter(dsn, min_size, max_size, self._statement_timeout_ms)

    @property
    def dialect(self) -> str:
        return "postgres"

    def execute(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        with self._impl.borrow() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params or None)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall() if cursor.description else []
                return columns, rows

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
            self._impl.close()
        except Exception:
            pass


class _PoolAdapter:
    """Uniform interface across psycopg2 and psycopg3 pools."""

    @contextmanager
    def borrow(self) -> Iterator[Any]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _Psycopg3PoolAdapter(_PoolAdapter):
    def __init__(self, pool: Any, timeout_ms: int) -> None:
        self._pool = pool
        self._timeout_ms = timeout_ms

    @contextmanager
    def borrow(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(_READ_ONLY_SETUP, {"timeout_ms": self._timeout_ms})
            yield conn

    def close(self) -> None:
        self._pool.close()


class _Psycopg2PoolAdapter(_PoolAdapter):
    """Minimal thread-safe pool for psycopg2 with read-only session setup."""

    def __init__(self, dsn: str, min_size: int, max_size: int, timeout_ms: int) -> None:
        import psycopg2  # type: ignore

        self._psycopg2 = psycopg2
        self._dsn = dsn
        self._max_size = max_size
        self._timeout_ms = timeout_ms
        self._lock = threading.Lock()
        self._idle: list[Any] = []
        self._in_use: int = 0
        for _ in range(min_size):
            self._idle.append(self._open())

    def _open(self) -> Any:
        conn = self._psycopg2.connect(self._dsn)
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute(_READ_ONLY_SETUP, {"timeout_ms": self._timeout_ms})
        return conn

    @contextmanager
    def borrow(self) -> Iterator[Any]:
        conn = self._acquire()
        try:
            yield conn
        finally:
            self._release(conn)

    def _acquire(self) -> Any:
        with self._lock:
            if self._idle:
                conn = self._idle.pop()
                self._in_use += 1
                return conn
            if self._in_use >= self._max_size:
                # Backpressure without a semaphore is crude — open a transient
                # connection rather than block indefinitely. Small pool sizes
                # in practice keep this rare.
                self._in_use += 1
                return self._open()
            self._in_use += 1
            return self._open()

    def _release(self, conn: Any) -> None:
        with self._lock:
            self._in_use -= 1
            if len(self._idle) < self._max_size:
                self._idle.append(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            for conn in self._idle:
                try:
                    conn.close()
                except Exception:
                    pass
            self._idle.clear()
