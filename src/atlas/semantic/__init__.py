"""Semantic layer / metrics DSL.

A small metric registry mapped by name. Each metric has:
* ``sql`` — a full SELECT that produces the metric result
* ``description`` — human text used for retrieval
* ``requires_tables`` — for policy check before rendering

At ask-time, if a user's question closely matches a metric name or description,
the analyst uses the metric's SQL directly (still subject to firewall + policy)
rather than asking the LLM to invent SQL. This is a slight but powerful pivot:
metrics are the correct answer; SQL generation is the fallback.

Metrics load from ``ATLAS_METRICS_FILE`` (YAML/JSON). Format::

    metrics:
      trips_per_status:
        description: "Number of trips grouped by status."
        requires_tables: [rides.trips]
        sql: |
          SELECT t.status AS status, COUNT(*) AS trip_count
          FROM rides.trips t
          GROUP BY t.status
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Metric:
    name: str
    description: str
    sql: str
    requires_tables: tuple[tuple[str, str], ...]


def _load_yaml(text: str):
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


class MetricRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._metrics: dict[str, Metric] = {}
        self._mtime: float | None = None
        self._lock = threading.Lock()
        if path:
            self._reload()

    def _reload(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            with self._lock:
                self._metrics = {}
                self._mtime = None
            return
        mtime = self._path.stat().st_mtime
        if self._mtime == mtime:
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        parsed = _load_yaml(raw) if raw.strip() else {}
        metrics: dict[str, Metric] = {}
        for name, entry in ((parsed or {}).get("metrics") or {}).items():
            if not isinstance(entry, dict) or "sql" not in entry:
                continue
            required = []
            for item in entry.get("requires_tables") or []:
                parts = str(item).split(".", 1)
                if len(parts) == 2:
                    required.append((parts[0], parts[1]))
            metrics[str(name)] = Metric(
                name=str(name),
                description=str(entry.get("description", "")),
                sql=str(entry["sql"]).strip(),
                requires_tables=tuple(required),
            )
        with self._lock:
            self._metrics = metrics
            self._mtime = mtime

    def all(self) -> list[Metric]:
        if self._path is not None:
            self._reload()
        return list(self._metrics.values())

    def find(self, question: str) -> Metric | None:
        """Naive lexical retrieval: return the metric whose name or description
        shares the most tokens with the question. Returns None if the best score
        is too low. Good enough for a portfolio demo; swap for embeddings
        (WS5.5) when embeddings are wired up.
        """
        metrics = self.all()
        if not metrics:
            return None
        q_tokens = set(_tokens(question))
        if not q_tokens:
            return None
        best: tuple[float, Metric | None] = (0.0, None)
        for metric in metrics:
            haystack = " ".join([metric.name.replace("_", " "), metric.description])
            m_tokens = set(_tokens(haystack))
            if not m_tokens:
                continue
            overlap = len(q_tokens & m_tokens)
            score = overlap / max(1, len(m_tokens | q_tokens))
            if score > best[0]:
                best = (score, metric)
        # Score threshold — tuned high enough to avoid gratuitous matches.
        if best[0] < 0.3:
            return None
        return best[1]


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text) if len(t) > 2]


_registry: MetricRegistry | None = None
_lock = threading.Lock()


def get_metric_registry() -> MetricRegistry:
    global _registry
    if _registry is not None:
        return _registry
    with _lock:
        if _registry is None:
            path_str = os.environ.get("ATLAS_METRICS_FILE")
            path = Path(path_str) if path_str else None
            _registry = MetricRegistry(path)
        return _registry


def reset_registry_for_tests() -> None:
    global _registry
    _registry = None
