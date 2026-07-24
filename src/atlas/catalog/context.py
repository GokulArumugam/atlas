"""Builds the little context blob we hand the text-to-SQL step — scoped per user."""

from __future__ import annotations

import re

from atlas.catalog.catalog import Catalog, TableInfo
from atlas.catalog.mindmap import MindMap
from atlas.policy.model import TableRef


def build_context(
    user: str,
    question: str,
    catalog: Catalog,
    mindmap: MindMap,
    max_tables: int = 6,
) -> str:
    """Return compact context made exclusively from tables visible to *user*."""
    visible = catalog.tables_for(user)
    if not visible or max_tables <= 0:
        return "Allowed catalog: no tables are available for this user."

    question_tokens = _tokens(question)
    ranked = sorted(
        ((_score(question_tokens, table), table) for table in visible),
        key=lambda item: (-item[0], str(item[1].ref)),
    )
    selected = [table for score, table in ranked if score > 0][:max_tables]
    if not selected:
        selected = [table for _, table in ranked[:max_tables]]

    selected_refs = {table.ref for table in selected}
    edges = mindmap.edges_for(user)
    # Include a directly connected visible table when there is space.  It prevents a
    # high-signal fact table from being handed to the planner without its join lookup.
    while len(selected) < max_tables:
        addition: TableRef | None = None
        for edge in edges:
            left = TableRef(edge.left.schema, edge.left.table)
            right = TableRef(edge.right.schema, edge.right.table)
            if left in selected_refs and right not in selected_refs:
                addition = right
            elif right in selected_refs and left not in selected_refs:
                addition = left
            if addition is not None:
                break
        if addition is None:
            break
        table = next(table for table in visible if table.ref == addition)
        selected.append(table)
        selected_refs.add(addition)

    selected.sort(key=lambda table: str(table.ref))
    selected_edges = mindmap.subgraph_for(user, [table.ref for table in selected])
    visible_refs = {table.ref for table in visible}
    hidden_table_words = {
        table.ref.table.lower()
        for table in catalog.all_tables()
        if table.ref not in visible_refs
    }
    lines = ["Allowed catalog (already scoped to this user):"]
    for table in selected:
        lines.append(f"TABLE {table.ref} (rows: {table.row_count})")
        columns = ", ".join(
            f"{column.column} {column.data_type}{' [PII]' if column.is_pii else ''}"
            for column in table.columns
        )
        lines.append(f"  Columns: {columns}")
        notes = _scoped_notes(table.description, hidden_table_words)
        if notes:
            lines.append(f"  Notes: {_compact(notes)}")
    lines.append("Join edges:")
    if selected_edges:
        lines.extend(
            f"  {edge.left} = {edge.right} ({edge.evidence}; confidence {edge.confidence:.2f})"
            for edge in selected_edges
        )
    else:
        lines.append("  None among the selected tables.")
    return "\n".join(lines)


def _score(question_tokens: set[str], table: TableInfo) -> int:
    haystack = " ".join(
        [table.ref.schema, table.ref.table, table.description]
        + [column.column for column in table.columns]
    )
    return len(question_tokens & _tokens(haystack))


def _tokens(value: str) -> set[str]:
    return {_normalise(token) for token in re.findall(r"[A-Za-z0-9]+", value.lower()) if token}


def _normalise(token: str) -> str:
    return token[:-2] if token.endswith("es") else token[:-1] if token.endswith("s") else token


def _compact(value: str) -> str:
    return " ".join(value.split())


def _scoped_notes(description: str, hidden_table_words: set[str]) -> str:
    """Drop semantic-note lines that name a table the current user cannot see."""
    safe_lines = []
    for line in description.splitlines():
        words = _tokens(line)
        if words & {_normalise(word) for word in hidden_table_words}:
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines).strip()
