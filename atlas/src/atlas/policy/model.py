"""Small immutable value objects shared between policy and enforcement layers."""

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class TableRef:
    schema: str
    table: str

    def __str__(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True)
class ColumnRef:
    schema: str
    table: str
    column: str

    def __str__(self) -> str:
        return f"{self.schema}.{self.table}.{self.column}"


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    MASK = "mask"


@dataclass
class Verdict:
    decision: Decision
    reason: str
    masked_columns: list[ColumnRef]
    denied_tables: list[TableRef]
    denied_columns: list[ColumnRef]
