"""Abstract connector interface — every warehouse backend implements this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ForeignKeyInfo:
    schema: str
    table: str
    column: str
    ref_schema: str
    ref_table: str
    ref_column: str


class WarehouseConnector(ABC):
    """Read-only adapter for one warehouse type.

    Concrete backends implement `dialect` and `execute`.  The default
    `query_history` and `foreign_keys` implementations return empty collections
    so backends that omit them still work — Atlas degrades gracefully to
    heuristic-only join graphs.
    """

    @property
    @abstractmethod
    def dialect(self) -> str:
        """SQLGlot dialect name used for SQL parsing and generation."""

    @abstractmethod
    def execute(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        """Run a read-only SQL statement; return (column_names, rows)."""

    def query_history(self) -> list[str]:
        """Return historical SQL strings for join-graph mining."""
        try:
            _, rows = self.execute("SELECT sql_text FROM meta.query_history")
            return [row[0] for row in rows]
        except Exception:
            return []

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        """Return declared FK relationships for join-graph seeding."""
        return []

    def close(self) -> None:
        pass

    def __enter__(self) -> "WarehouseConnector":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
