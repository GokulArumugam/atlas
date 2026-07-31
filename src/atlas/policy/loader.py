"""YAML/JSON policy loader with mtime-based hot reload.

Design goals:
* Zero external deps for JSON. YAML is optional — falls back to JSON if
  PyYAML is unavailable.
* The loaded config is *structurally identical* to the hardcoded
  ``POLICY_CONFIG`` dict, so ``PolicyEngine`` doesn't care where it came
  from.
* Adds a new ``row_predicates`` key per user (user_id → {schema.table: SQL})
  that ``SqlFirewall`` uses to inject row-level security predicates.

File format::

    users:
      gokul:
        team: engineering
        visible_tables: [rides.locations, rides.trips]
        unmasked_pii: []
        row_predicates:
          rides.trips: "t.status = 'completed'"

    tables:
      rides:
        trips: [id, rider_id, driver_id, status]

    pii_columns:
      - hr.employees.salary

    column_types:
      hr.employees.salary: numeric

    column_masking_expressions:
      hr.employees.salary: "NULL"

    masking_expression: "'***MASKED***'"
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def _load_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


class PolicyLoader:
    """Load and hot-reload a policy file from disk."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime: float | None = None
        self._config: dict | None = None
        self._lock = threading.Lock()

    def get(self) -> dict | None:
        """Return the current policy dict, reloading if the file changed."""
        with self._lock:
            if not self._path.exists():
                return None
            mtime = self._path.stat().st_mtime
            if self._mtime == mtime and self._config is not None:
                return self._config
            try:
                raw = self._path.read_text(encoding="utf-8")
            except OSError:
                return self._config
            parsed = _load_yaml(raw) if raw.strip() else {}
            if not isinstance(parsed, dict):
                return self._config
            self._config = _normalize(parsed)
            self._mtime = mtime
            return self._config


def _normalize(raw: dict) -> dict:
    """Turn user-friendly keys into the shape ``PolicyEngine`` expects."""

    tables = _normalize_tables(raw.get("tables", {}))
    pii_columns = _normalize_pii(raw.get("pii_columns", []))
    column_types = _normalize_column_scoped(raw.get("column_types", {}))
    column_masks = _normalize_column_scoped(raw.get("column_masking_expressions", {}))

    users_out: dict[str, dict] = {}
    for user_id, entry in (raw.get("users") or {}).items():
        if not isinstance(entry, dict):
            continue
        visible = set()
        for item in entry.get("visible_tables", []) or []:
            parts = str(item).split(".", 1)
            if len(parts) == 2:
                visible.add((parts[0], parts[1]))
        unmasked = set()
        for item in entry.get("unmasked_pii", []) or []:
            parts = str(item).split(".")
            if len(parts) == 3:
                unmasked.add(tuple(parts))
        row_predicates: dict[tuple[str, str], str] = {}
        for key, predicate in (entry.get("row_predicates") or {}).items():
            parts = str(key).split(".", 1)
            if len(parts) == 2 and isinstance(predicate, str) and predicate.strip():
                row_predicates[(parts[0], parts[1])] = predicate.strip()
        users_out[str(user_id)] = {
            "team": str(entry.get("team", "unknown")),
            "visible_tables": visible,
            "unmasked_pii": unmasked,
            "row_predicates": row_predicates,
        }

    return {
        "tables": tables,
        "pii_columns": pii_columns,
        "users": users_out,
        "column_types": column_types,
        "column_masking_expressions": column_masks,
        "masking_expression": raw.get("masking_expression", "'***MASKED***'"),
    }


def _normalize_tables(raw: dict) -> dict:
    out: dict[str, dict[str, set[str]]] = {}
    for schema, tables in (raw or {}).items():
        if not isinstance(tables, dict):
            continue
        out[str(schema)] = {}
        for table, columns in tables.items():
            if isinstance(columns, list):
                out[str(schema)][str(table)] = {str(c) for c in columns}
    return out


def _normalize_pii(raw: list) -> set[tuple[str, str, str]]:
    result = set()
    for entry in raw or []:
        parts = str(entry).split(".")
        if len(parts) == 3:
            result.add(tuple(parts))
    return result


def _normalize_column_scoped(raw: dict) -> dict:
    result = {}
    for key, value in (raw or {}).items():
        parts = str(key).split(".")
        if len(parts) == 3:
            result[tuple(parts)] = str(value) if not isinstance(value, str) else value
    return result


_loader: PolicyLoader | None = None
_lock = threading.Lock()


def get_policy_loader() -> PolicyLoader | None:
    """Return the process-wide loader if ``ATLAS_POLICY_FILE`` is set."""

    global _loader
    if _loader is not None:
        return _loader
    with _lock:
        if _loader is not None:
            return _loader
        path_str = os.environ.get("ATLAS_POLICY_FILE")
        if not path_str:
            return None
        _loader = PolicyLoader(Path(path_str))
        return _loader


def reset_policy_loader_for_tests() -> None:
    global _loader
    _loader = None
