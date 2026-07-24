"""Small immutable value objects shared between policy and enforcement layers."""

from dataclasses import dataclass
from enum import Enum


# Friendly plural names for PII columns, shared by the policy engine and the
# firewall so their user-facing messages stay consistent (one source of truth).
_PII_DISPLAY_NAMES = {
    "phone": "Phone numbers",
    "email": "Email addresses",
    "pan": "PAN numbers",
    "full_name": "Full names",
    "salary": "Salaries",
}


def display_name(column: str) -> str:
    """Turn a column name like 'full_name' into 'Full names' for messages."""
    return _PII_DISPLAY_NAMES.get(column, column.replace("_", " ").capitalize())


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
