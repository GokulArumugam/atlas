"""Query cost estimation.

Runs the warehouse-native EXPLAIN and applies a per-user cost policy:
* Estimated scan bytes above `max_scan_bytes` → deny.
* Estimated result rows above `max_result_rows` → deny.

DuckDB EXPLAIN doesn't expose bytes cleanly, so we parse the row estimate.
Postgres EXPLAIN (FORMAT JSON) does, and we consume it directly.

The check is best-effort — if the estimate can't be extracted, we default to
allow (fail-open here is deliberate: cost estimation is a hint, not a security
control). Row-count and byte thresholds live in the policy config under
``cost_limits`` (per-user) or ``default_cost_limits`` (fallback).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from atlas.connector.base import WarehouseConnector


@dataclass(frozen=True)
class CostEstimate:
    rows: int | None
    bytes: int | None
    source: str  # 'duckdb' | 'postgres' | 'unknown'


@dataclass(frozen=True)
class CostVerdict:
    allowed: bool
    estimate: CostEstimate
    reason: str


class CostEstimator:
    def __init__(self, connector: WarehouseConnector) -> None:
        self._connector = connector

    def estimate(self, sql: str) -> CostEstimate:
        dialect = self._connector.dialect
        try:
            if dialect == "postgres":
                return self._postgres(sql)
            if dialect == "duckdb":
                return self._duckdb(sql)
        except Exception:
            pass
        return CostEstimate(rows=None, bytes=None, source="unknown")

    def _duckdb(self, sql: str) -> CostEstimate:
        # DuckDB EXPLAIN prints per-node row estimates like `~20000 Rows`.
        # The bottom-most scan gives the cardinality we care about for a cost
        # gate; that's the *max* estimate anywhere in the plan.
        _, rows = self._connector.execute(f"EXPLAIN {sql}")
        text = "\n".join(str(v) for row in rows for v in row)
        estimates = [int(m) for m in re.findall(r"~(\d+)\s+Rows", text)]
        estimated_rows = max(estimates) if estimates else None
        return CostEstimate(rows=estimated_rows, bytes=None, source="duckdb")

    def _postgres(self, sql: str) -> CostEstimate:
        _, rows = self._connector.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        if not rows:
            return CostEstimate(rows=None, bytes=None, source="postgres")
        try:
            plan = rows[0][0]
            if isinstance(plan, str):
                plan = json.loads(plan)
            top = plan[0]["Plan"]
            row_count = int(top.get("Plan Rows", 0))
            width = int(top.get("Plan Width", 0))
            return CostEstimate(rows=row_count, bytes=row_count * width, source="postgres")
        except (KeyError, IndexError, ValueError, TypeError):
            return CostEstimate(rows=None, bytes=None, source="postgres")


def check_cost(estimate: CostEstimate, limits: dict) -> CostVerdict:
    max_rows = limits.get("max_result_rows")
    max_bytes = limits.get("max_scan_bytes")
    if max_rows is not None and estimate.rows is not None and estimate.rows > max_rows:
        return CostVerdict(
            allowed=False,
            estimate=estimate,
            reason=(
                f"Estimated result rows {estimate.rows:,} exceeds the policy "
                f"limit of {max_rows:,}."
            ),
        )
    if max_bytes is not None and estimate.bytes is not None and estimate.bytes > max_bytes:
        return CostVerdict(
            allowed=False,
            estimate=estimate,
            reason=(
                f"Estimated scan bytes {estimate.bytes:,} exceeds the policy "
                f"limit of {max_bytes:,}."
            ),
        )
    return CostVerdict(allowed=True, estimate=estimate, reason="within cost limits")


def limits_for_user(config: dict, user: str) -> dict:
    """Return the cost limits dict for `user`, merging defaults + per-user."""
    defaults = dict(config.get("default_cost_limits") or {})
    per_user = ((config.get("users") or {}).get(user) or {}).get("cost_limits") or {}
    defaults.update(per_user)
    return defaults
