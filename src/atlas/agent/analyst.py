"""The runtime analyst: scope it, generate SQL, enforce, run, and log."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from numbers import Number
from time import perf_counter

from atlas.agent.generator import (
    GeneratorUnavailable,
    SqlGenerator,
    UnsupportedQuestion,
    default_generator,
)
from atlas.audit.audit import AuditLog
from atlas.cache import TTLLRUCache
from atlas.catalog.catalog import Catalog
from atlas.catalog.context import build_context
from atlas.catalog.mindmap import MindMap
from atlas.connector.base import WarehouseConnector
from atlas.firewall.firewall import FirewallResult, SqlFirewall
from atlas.observability.otel import get_tracer
from atlas.policy.cost import CostEstimator, check_cost, limits_for_user
from atlas.policy.engine import PolicyEngine
from atlas.policy.model import Decision
from atlas.semantic import get_metric_registry


@dataclass
class AnalystAnswer:
    decision: Decision
    reason: str
    question: str
    sql: str | None
    columns: list[str]
    rows: list[tuple]
    chart: dict | None
    tables_touched: list[str]
    masked_columns: list[str]
    latency_ms: dict
    audit_id: str
    mermaid: str


class Analyst:
    """Runs the whole governed path for one question.

    There's one boundary I care about a lot: `_execute_safe_sql` only ever gets
    `FirewallResult.safe_sql` — never the raw SQL the generator produced. Keeping
    that call separate makes it easy to prove two things in tests: a denial never
    touches the warehouse, and an allow/mask runs exactly the SQL the firewall
    approved, byte for byte.
    """

    def __init__(
        self,
        connector: WarehouseConnector,
        audit_path: str = "data/audit.duckdb",
        generator: SqlGenerator | None = None,
        *,
        sql_cache_size: int = 512,
        sql_cache_ttl_seconds: float = 300.0,
    ) -> None:
        self.connector = connector
        self.policy = PolicyEngine()
        self.catalog = Catalog(connector, self.policy)
        self.mindmap = MindMap(self.catalog)
        self.mindmap.build(connector)
        self.firewall = SqlFirewall(self.policy, dialect=connector.dialect)
        self.cost_estimator = CostEstimator(connector)
        self.generator = generator or default_generator()
        self.audit = AuditLog(audit_path)
        # (user_scope_hash, normalized_question) -> generated SQL text.
        # We cache the *generator output* not the *executed rows* — rows can
        # change under the caller, but the SQL is a pure function of scope +
        # question. The firewall still runs on every hit.
        self._sql_cache: TTLLRUCache[tuple[str, str], str] = TTLLRUCache(
            max_size=sql_cache_size, ttl_seconds=sql_cache_ttl_seconds
        )

    def ask(
        self,
        user: str,
        question: str,
        *,
        generator: SqlGenerator | None = None,
    ) -> AnalystAnswer:
        """Answer one question without ever executing unapproved generator SQL.

        ``generator`` optionally overrides the default generator for this call —
        used by the API when the caller supplies a provider profile or an
        ephemeral provider config.
        """
        with get_tracer().start_as_current_span("atlas.analyst.ask") as span:
            span.set_attribute("atlas.user", user)
            span.set_attribute("atlas.question_length", len(question))
            answer = self._ask_inner(user, question, generator=generator)
            span.set_attribute("atlas.decision", answer.decision.value)
            span.set_attribute("atlas.rows", len(answer.rows))
            span.set_attribute("atlas.audit_id", answer.audit_id)
            return answer

    def _ask_inner(
        self,
        user: str,
        question: str,
        *,
        generator: SqlGenerator | None = None,
    ) -> AnalystAnswer:
        active_generator = generator or self.generator
        started = perf_counter()
        latency_ms: dict[str, int] = {"context": 0, "generate": 0, "firewall": 0, "execute": 0, "total": 0}

        context_started = perf_counter()
        context = build_context(user, question, self.catalog, self.mindmap)
        mermaid = self.mindmap.to_mermaid(user)
        latency_ms["context"] = _elapsed_ms(context_started)

        generated_sql: str | None = None
        generation_started = perf_counter()
        cache_key = self._cache_key(user, question)
        cached_sql = self._sql_cache.get(cache_key)
        # WS5.1 semantic layer: if the question matches a curated metric, use
        # its SQL directly. Metrics beat cache and generator — they represent
        # a human-signed-off correct answer.
        matched_metric = get_metric_registry().find(question)
        if matched_metric is not None:
            generated_sql = matched_metric.sql
        elif cached_sql is not None:
            generated_sql = cached_sql
        else:
            try:
                generated_sql = active_generator.generate(user, question, context)
                self._sql_cache.set(cache_key, generated_sql)
            except Exception as error:
                # Three failure classes: user's phrasing is out of scope for the
                # (deterministic) generator, the model itself is unreachable, or
                # an unexpected crash. Each surfaces a distinct, actionable
                # message so the user knows what to fix.
                if isinstance(error, UnsupportedQuestion):
                    reason = str(error)
                elif isinstance(error, GeneratorUnavailable):
                    reason = str(error)
                else:
                    reason = "I couldn't generate a safe query for that question."
                latency_ms["generate"] = _elapsed_ms(generation_started)
                latency_ms["total"] = _elapsed_ms(started)
                return self._audited_answer(
                    user=user,
                    question=question,
                    mermaid=mermaid,
                    generated_sql=None,
                    result=None,
                    decision=Decision.DENY,
                    reason=reason,
                    columns=[],
                    rows=[],
                    chart=None,
                    latency_ms=latency_ms,
                )
        latency_ms["generate"] = _elapsed_ms(generation_started)

        firewall_started = perf_counter()
        result = self.firewall.check(user, generated_sql)
        latency_ms["firewall"] = _elapsed_ms(firewall_started)
        if result.decision is Decision.DENY:
            latency_ms["total"] = _elapsed_ms(started)
            return self._audited_answer(
                user=user,
                question=question,
                mermaid=mermaid,
                generated_sql=generated_sql,
                result=result,
                decision=result.decision,
                reason=result.reason,
                columns=[],
                rows=[],
                chart=None,
                latency_ms=latency_ms,
            )

        # The one rule that matters: safe_sql is the ONLY SQL that ever reaches
        # the warehouse. The raw generator output above is kept purely for the audit record.
        safe_sql = result.safe_sql
        if safe_sql is None:  # Defensive fail-closed guard for any future firewall change.
            latency_ms["total"] = _elapsed_ms(started)
            return self._audited_answer(
                user=user,
                question=question,
                mermaid=mermaid,
                generated_sql=generated_sql,
                result=result,
                decision=Decision.DENY,
                reason="I can't safely verify that SQL query, so I won't run it.",
                columns=[],
                rows=[],
                chart=None,
                latency_ms=latency_ms,
            )

        # WS4.3 cost check: run EXPLAIN on the firewall-approved SQL and
        # compare against the user's policy limits. This is a best-effort
        # gate; unknown estimates never block. Only DENY when we have a
        # concrete over-limit estimate.
        limits = limits_for_user(self.policy.config, user)
        if limits:
            estimate = self.cost_estimator.estimate(safe_sql)
            verdict = check_cost(estimate, limits)
            if not verdict.allowed:
                latency_ms["total"] = _elapsed_ms(started)
                return self._audited_answer(
                    user=user,
                    question=question,
                    mermaid=mermaid,
                    generated_sql=generated_sql,
                    result=result,
                    decision=Decision.DENY,
                    reason=verdict.reason,
                    columns=[],
                    rows=[],
                    chart=None,
                    latency_ms=latency_ms,
                )

        execution_started = perf_counter()
        columns: list[str] = []
        rows: list[tuple] = []
        reason = result.reason
        chart: dict | None = None
        try:
            columns, rows = self._execute_safe_sql(safe_sql)
            chart = self._chart_for(question, columns, rows)
        except Exception:
            reason = "The approved query could not be executed against the warehouse."
        latency_ms["execute"] = _elapsed_ms(execution_started)
        latency_ms["total"] = _elapsed_ms(started)
        return self._audited_answer(
            user=user,
            question=question,
            mermaid=mermaid,
            generated_sql=generated_sql,
            result=result,
            decision=result.decision,
            reason=reason,
            columns=columns,
            rows=rows,
            chart=chart,
            latency_ms=latency_ms,
        )

    def _execute_safe_sql(self, safe_sql: str) -> tuple[list[str], list[tuple]]:
        """Run exactly the firewall-approved SQL through the warehouse connector."""
        return self.connector.execute(safe_sql)

    def run_sql(self, user: str, sql: str, label: str = "") -> AnalystAnswer:
        """Run user-edited SQL through the full governed pipeline.

        This is the entry point behind the "Run" button on the SQL editor. It
        bypasses the LLM/deterministic generator (the user typed the SQL
        themselves) but still goes through:

            firewall.check(user, sql)  →  cost estimate  →  connector.execute
            → chart shape           →  audited answer  →  hash-chained log

        The user's identity is authoritative; anything they can't see through
        their policy scope will fail the firewall the same way an LLM-produced
        query would.
        """
        with get_tracer().start_as_current_span("atlas.analyst.run_sql") as span:
            span.set_attribute("atlas.user", user)
            span.set_attribute("atlas.sql_length", len(sql))
            answer = self._run_sql_inner(user, sql, label)
            span.set_attribute("atlas.decision", answer.decision.value)
            span.set_attribute("atlas.rows", len(answer.rows))
            span.set_attribute("atlas.audit_id", answer.audit_id)
            return answer

    def _run_sql_inner(self, user: str, sql: str, label: str) -> AnalystAnswer:
        started = perf_counter()
        latency_ms: dict[str, int] = {
            "context": 0, "generate": 0, "firewall": 0, "execute": 0, "total": 0,
        }
        mermaid = self.mindmap.to_mermaid(user)
        question = label.strip() or "Edited SQL"

        # Firewall on the raw user SQL.
        firewall_started = perf_counter()
        result = self.firewall.check(user, sql)
        latency_ms["firewall"] = _elapsed_ms(firewall_started)

        if result.decision is Decision.DENY:
            latency_ms["total"] = _elapsed_ms(started)
            return self._audited_answer(
                user=user, question=question, mermaid=mermaid,
                generated_sql=sql, result=result,
                decision=result.decision, reason=result.reason,
                columns=[], rows=[], chart=None, latency_ms=latency_ms,
            )

        safe_sql = result.safe_sql
        if safe_sql is None:
            latency_ms["total"] = _elapsed_ms(started)
            return self._audited_answer(
                user=user, question=question, mermaid=mermaid,
                generated_sql=sql, result=result,
                decision=Decision.DENY,
                reason="I can't safely verify that SQL query, so I won't run it.",
                columns=[], rows=[], chart=None, latency_ms=latency_ms,
            )

        # Cost check (per-user policy limits).
        limits = limits_for_user(self.policy.config, user)
        if limits:
            estimate = self.cost_estimator.estimate(safe_sql)
            verdict = check_cost(estimate, limits)
            if not verdict.allowed:
                latency_ms["total"] = _elapsed_ms(started)
                return self._audited_answer(
                    user=user, question=question, mermaid=mermaid,
                    generated_sql=sql, result=result,
                    decision=Decision.DENY, reason=verdict.reason,
                    columns=[], rows=[], chart=None, latency_ms=latency_ms,
                )

        # Execute the firewall-approved SQL.
        execution_started = perf_counter()
        columns: list[str] = []
        rows: list[tuple] = []
        reason = result.reason
        chart: dict | None = None
        try:
            columns, rows = self._execute_safe_sql(safe_sql)
            chart = self._chart_for(question, columns, rows)
        except Exception:
            reason = "The approved query could not be executed against the warehouse."
        latency_ms["execute"] = _elapsed_ms(execution_started)
        latency_ms["total"] = _elapsed_ms(started)
        return self._audited_answer(
            user=user, question=question, mermaid=mermaid,
            generated_sql=sql, result=result,
            decision=result.decision, reason=reason,
            columns=columns, rows=rows, chart=chart, latency_ms=latency_ms,
        )


    def _cache_key(self, user: str, question: str) -> tuple[str, str]:
        """Cache key that binds to the user's *scope*, not just their username.

        Two users with identical scope share cache entries; a scope change on
        the same user invalidates their entries automatically.
        """
        scope = self.policy.visible_tables(user)
        scope_bytes = "|".join(sorted(f"{t.schema}.{t.table}" for t in scope)).encode("utf-8")
        scope_hash = hashlib.sha256(scope_bytes).hexdigest()[:16]
        normalized = " ".join(question.strip().lower().split())
        return scope_hash, normalized

    def cache_stats(self) -> dict:
        return self._sql_cache.stats()

    def _audited_answer(
        self,
        *,
        user: str,
        question: str,
        mermaid: str,
        generated_sql: str | None,
        result: FirewallResult | None,
        decision: Decision,
        reason: str,
        columns: list[str],
        rows: list[tuple],
        chart: dict | None,
        latency_ms: dict,
    ) -> AnalystAnswer:
        audit = self.audit.record(
            user=user,
            team=self.policy.team_of(user),
            question=question,
            generated_sql=generated_sql,
            executed_sql=result.safe_sql if result and decision is not Decision.DENY else None,
            decision=decision.value,
            reason=reason,
            tables_touched=[str(table) for table in result.tables_touched] if result else [],
            columns_touched=[str(column) for column in result.columns_touched] if result else [],
            masked_columns=[str(column) for column in result.masked_columns] if result else [],
            row_count=len(rows) if result and decision is not Decision.DENY else None,
            latency_ms=latency_ms,
        )
        return AnalystAnswer(
            decision=decision,
            reason=reason,
            question=question,
            sql=result.safe_sql if result and decision is not Decision.DENY else None,
            columns=columns,
            rows=rows,
            chart=chart,
            tables_touched=[str(table) for table in result.tables_touched] if result else [],
            masked_columns=[str(column) for column in result.masked_columns] if result else [],
            latency_ms=latency_ms,
            audit_id=audit.audit_id,
            mermaid=mermaid,
        )

    @staticmethod
    def _chart_for(question: str, columns: list[str], rows: list[tuple]) -> dict:
        title = question.strip() or "Query result"
        base = {"mark": "table", "x": None, "y": None, "title": title, "vega_lite": None}
        if len(columns) == 1 and len(rows) == 1:
            base.update({"mark": "value", "x": None, "y": columns[0]})
            return base
        if len(columns) == 2 and rows:
            x_value, y_value = rows[0]
            if _is_date(x_value) or "date" in columns[0].lower():
                spec = {
                    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                    "title": title,
                    "data": {"values": [{columns[0]: r[0], columns[1]: r[1]} for r in rows]},
                    "mark": "line",
                    "encoding": {
                        "x": {"field": columns[0], "type": "temporal"},
                        "y": {"field": columns[1], "type": "quantitative"},
                    },
                }
                base.update({"mark": "line", "x": columns[0], "y": columns[1], "vega_lite": spec})
                return base
            if not isinstance(x_value, (Number, bool)) and isinstance(y_value, Number):
                spec = {
                    "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
                    "title": title,
                    "data": {"values": [{columns[0]: r[0], columns[1]: r[1]} for r in rows]},
                    "mark": "bar",
                    "encoding": {
                        "x": {"field": columns[0], "type": "nominal"},
                        "y": {"field": columns[1], "type": "quantitative"},
                    },
                }
                base.update({"mark": "bar", "x": columns[0], "y": columns[1], "vega_lite": spec})
                return base
        return base


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)


def _is_date(value: object) -> bool:
    return isinstance(value, (date, datetime))
