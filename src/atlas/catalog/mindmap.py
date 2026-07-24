"""Mine a conservative join graph from query history, names, and constraints."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

import duckdb
from sqlglot import exp, parse_one

from atlas.catalog.catalog import Catalog
from atlas.policy.model import ColumnRef, TableRef


@dataclass(frozen=True)
class JoinEdge:
    left: ColumnRef
    right: ColumnRef
    confidence: float
    evidence: str


class MindMap:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._edges: list[JoinEdge] = []

    def build(self, db_path: str) -> None:
        """Learn and rank joins, preferring relationships humans actually used."""
        history = self._history_edges(db_path)
        constraints = self._constraint_edges(db_path)
        heuristics = self._heuristic_edges()
        candidates: dict[tuple[ColumnRef, ColumnRef], JoinEdge] = {}

        # Insert in descending source confidence.  A canonical key removes reversed
        # duplicates while preserving the natural FK-to-PK orientation of the first
        # observed edge for readable output.
        for key, count, edge in history:
            candidates[key] = JoinEdge(
                edge.left, edge.right, 0.99,
                f"seen in {count} historical queries",
            )
        for key, edge in constraints.items():
            candidates.setdefault(key, JoinEdge(edge.left, edge.right, 0.90, "declared foreign-key constraint"))
        for key, edge in heuristics.items():
            candidates.setdefault(key, JoinEdge(edge.left, edge.right, 0.65, "name+type heuristic"))

        self._edges = sorted(
            candidates.values(),
            key=lambda edge: (-edge.confidence, str(edge.left), str(edge.right)),
        )

    def edges_for(self, user: str) -> list[JoinEdge]:
        visible = {table.ref for table in self.catalog.tables_for(user)}
        return [
            edge for edge in self._edges
            if _table_of(edge.left) in visible and _table_of(edge.right) in visible
        ]

    def subgraph_for(self, user: str, tables: list[TableRef]) -> list[JoinEdge]:
        requested = set(tables)
        return [
            edge for edge in self.edges_for(user)
            if _table_of(edge.left) in requested and _table_of(edge.right) in requested
        ]

    def to_mermaid(self, user: str) -> str:
        """Render only the user's graph; invisible names cannot enter this string."""
        edges = self.edges_for(user)
        refs = sorted(
            {_table_of(edge.left) for edge in edges} | {_table_of(edge.right) for edge in edges},
            key=str,
        )
        lines = ["flowchart LR"]
        for ref in refs:
            lines.append(f'  {_node_id(ref)}["{ref}"]')
        for edge in edges:
            lines.append(
                f'  {_node_id(_table_of(edge.left))} -->|"{edge.left.column} = '
                f'{edge.right.column} ({edge.evidence})"| {_node_id(_table_of(edge.right))}'
            )
        return "\n".join(lines)

    def _history_edges(self, db_path: str) -> list[tuple[tuple[ColumnRef, ColumnRef], int, JoinEdge]]:
        connection = duckdb.connect(db_path, read_only=True)
        try:
            sql_rows = connection.execute("SELECT sql_text FROM meta.query_history").fetchall()
        finally:
            connection.close()

        counts: Counter[tuple[ColumnRef, ColumnRef]] = Counter()
        examples: dict[tuple[ColumnRef, ColumnRef], JoinEdge] = {}
        for (sql_text,) in sql_rows:
            try:
                expression = parse_one(sql_text, read="duckdb")
            except Exception:
                continue
            aliases = _table_aliases(expression)
            # Count a relationship once per historical query, even when an unusual
            # query repeats the same equality in more than one predicate.
            seen_in_query: set[tuple[ColumnRef, ColumnRef]] = set()
            for join in expression.find_all(exp.Join):
                on = join.args.get("on")
                if on is None:
                    continue
                for equality in on.find_all(exp.EQ):
                    left, right = equality.left, equality.right
                    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                        continue
                    left_ref = _resolve_column(left, aliases)
                    right_ref = _resolve_column(right, aliases)
                    if left_ref is None or right_ref is None or left_ref == right_ref:
                        continue
                    key = _edge_key(left_ref, right_ref)
                    seen_in_query.add(key)
                    examples.setdefault(key, JoinEdge(left_ref, right_ref, 0.0, ""))
            counts.update(seen_in_query)
        return [(key, count, examples[key]) for key, count in counts.items()]

    def _heuristic_edges(self) -> dict[tuple[ColumnRef, ColumnRef], JoinEdge]:
        tables = self.catalog.all_tables()
        ids: dict[str, list[ColumnInfoLike]] = {}
        for table in tables:
            for column in table.columns:
                if column.column == "id":
                    ids.setdefault(_singular(table.ref.table), []).append(column)

        edges: dict[tuple[ColumnRef, ColumnRef], JoinEdge] = {}
        for table in tables:
            for column in table.columns:
                match = re.fullmatch(r"(.+)_id", column.column)
                if not match:
                    continue
                target_name = _singular(match.group(1).split("_")[-1])
                for target in ids.get(target_name, []):
                    if column.data_type != target.data_type:
                        continue
                    left = ColumnRef(column.schema, column.table, column.column)
                    right = ColumnRef(target.schema, target.table, target.column)
                    key = _edge_key(left, right)
                    edges.setdefault(key, JoinEdge(left, right, 0.0, ""))
        return edges

    def _constraint_edges(self, db_path: str) -> dict[tuple[ColumnRef, ColumnRef], JoinEdge]:
        connection = duckdb.connect(db_path, read_only=True)
        try:
            rows = connection.execute(
                """
                SELECT schema_name, table_name, constraint_text, constraint_column_names
                FROM duckdb_constraints()
                WHERE constraint_type = 'FOREIGN KEY'
                """
            ).fetchall()
        except Exception:
            return {}
        finally:
            connection.close()

        edges: dict[tuple[ColumnRef, ColumnRef], JoinEdge] = {}
        pattern = re.compile(
            r"REFERENCES\s+(?:(?P<schema>[A-Za-z_]\w*)\.)?(?P<table>[A-Za-z_]\w*)\s*\((?P<column>[A-Za-z_]\w*)\)",
            re.IGNORECASE,
        )
        for schema, table, constraint_text, columns in rows:
            match = pattern.search(constraint_text)
            if not match or not columns or len(columns) != 1:
                continue
            target_schema = match.group("schema") or schema
            left = ColumnRef(schema, table, columns[0])
            right = ColumnRef(target_schema, match.group("table"), match.group("column"))
            edges.setdefault(_edge_key(left, right), JoinEdge(left, right, 0.0, ""))
        return edges


# Structural typing avoids importing a second catalog dataclass solely for a local map.
class ColumnInfoLike:
    schema: str
    table: str
    column: str
    data_type: str


def _table_aliases(expression: exp.Expression) -> dict[str, TableRef]:
    aliases: dict[str, TableRef] = {}
    for table in expression.find_all(exp.Table):
        if not table.name:
            continue
        ref = TableRef(table.db or "main", table.name)
        aliases[table.alias_or_name] = ref
        aliases.setdefault(table.name, ref)
    return aliases


def _resolve_column(column: exp.Column, aliases: dict[str, TableRef]) -> ColumnRef | None:
    if not column.table:
        return None
    table = aliases.get(column.table)
    return ColumnRef(table.schema, table.table, column.name) if table else None


def _edge_key(left: ColumnRef, right: ColumnRef) -> tuple[ColumnRef, ColumnRef]:
    return tuple(sorted((left, right), key=str))  # type: ignore[return-value]


def _table_of(column: ColumnRef) -> TableRef:
    return TableRef(column.schema, column.table)


def _singular(word: str) -> str:
    return word[:-2] if word.endswith("es") else word[:-1] if word.endswith("s") else word


def _node_id(ref: TableRef) -> str:
    return re.sub(r"\W", "_", f"{ref.schema}_{ref.table}")
