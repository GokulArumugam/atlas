"""The runtime analyst: scope it, generate SQL, enforce, run, and log."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from numbers import Number
from time import perf_counter

import duckdb

from atlas.agent.generator import SqlGenerator, UnsupportedQuestion, default_generator
from atlas.audit.audit import AuditLog
from atlas.catalog.catalog import Catalog
from atlas.catalog.context import build_context
from atlas.catalog.mindmap import MindMap
from atlas.firewall.firewall import FirewallResult, SqlFirewall
from atlas.policy.engine import PolicyEngine
from atlas.policy.model import Decision


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
        db_path: str = "data/warehouse.duckdb",
        audit_path: str = "data/audit.duckdb",
        generator: SqlGenerator | None = None,
    ) -> None:
        self.db_path = str(db_path)
        self.policy = PolicyEngine()
        self.catalog = Catalog(self.db_path, self.policy)
        self.mindmap = MindMap(self.catalog)
        self.mindmap.build(self.db_path)
        self.firewall = SqlFirewall(self.policy)
        self.generator = generator or default_generator()
        self.audit = AuditLog(audit_path)

    def ask(self, user: str, question: str) -> AnalystAnswer:
        """Answer one question without ever executing unapproved generator SQL."""
        started = perf_counter()
        latency_ms: dict[str, int] = {"context": 0, "generate": 0, "firewall": 0, "execute": 0, "total": 0}

        context_started = perf_counter()
        context = build_context(user, question, self.catalog, self.mindmap)
        mermaid = self.mindmap.to_mermaid(user)
        latency_ms["context"] = _elapsed_ms(context_started)

        generated_sql: str | None = None
        generation_started = perf_counter()
        try:
            generated_sql = self.generator.generate(user, question, context)
        except Exception as error:
            # A known "I don't understand that question" and an unexpected generator
            # failure both end the same way: a clean denial, nothing executed.
            reason = (
                str(error)
                if isinstance(error, UnsupportedQuestion)
                else "I couldn't generate a safe query for that question."
            )
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
        # DuckDB. The raw generator output above is kept purely for the audit record.
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
        """Run exactly the firewall-approved SQL through a read-only connection."""
        connection = duckdb.connect(self.db_path, read_only=True)
        try:
            cursor = connection.execute(safe_sql)
            columns = [description[0] for description in cursor.description]
            return columns, cursor.fetchall()
        finally:
            connection.close()

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
        if len(columns) == 1 and len(rows) == 1:
            return {"mark": "value", "x": None, "y": columns[0], "title": title}
        if len(columns) == 2 and rows:
            x_value, y_value = rows[0]
            if _is_date(x_value) or "date" in columns[0].lower():
                return {"mark": "line", "x": columns[0], "y": columns[1], "title": title}
            if not isinstance(x_value, (Number, bool)) and isinstance(y_value, Number):
                return {"mark": "bar", "x": columns[0], "y": columns[1], "title": title}
        return {"mark": "table", "x": None, "y": None, "title": title}


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)


def _is_date(value: object) -> bool:
    return isinstance(value, (date, datetime))
