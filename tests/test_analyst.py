from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.agent.analyst import Analyst
from atlas.agent.generator import DeterministicGenerator
from atlas.connector import DuckDBConnector
from atlas.connector.base import ForeignKeyInfo, WarehouseConnector
from atlas.policy.model import Decision


def _analyst(tmp_path, connector=None) -> Analyst:
    if connector is None:
        connector = DuckDBConnector("data/warehouse.duckdb")
    return Analyst(
        connector=connector,
        audit_path=str(tmp_path / "audit.duckdb"),
        generator=DeterministicGenerator(),
    )


def test_documented_demo_questions_are_governed_and_audited(tmp_path):
    analyst = _analyst(tmp_path)
    priya = analyst.ask("priya", "average salary by department")
    denied = analyst.ask("gokul", "average salary by department")
    phones = analyst.ask("gokul", "show me riders' phone numbers")
    riders = analyst.ask("gokul", "average riders from Airport to Downtown")
    daily = analyst.ask("gokul", "trips per day")

    assert priya.decision is Decision.ALLOW
    assert priya.rows and priya.chart and priya.chart["mark"] == "bar"
    assert denied.decision is Decision.DENY
    assert denied.rows == [] and denied.sql is None
    assert "engineering" in denied.reason.lower()
    assert phones.decision is Decision.MASK
    assert phones.rows and all(value == "***MASKED***" for row in phones.rows for value in row)
    assert riders.decision is Decision.ALLOW
    assert riders.rows and abs(riders.rows[0][0] - 2.5) < 0.1
    assert daily.decision is Decision.ALLOW
    assert daily.chart and daily.chart["mark"] == "line"

    recorded = {entry.audit_id for entry in analyst.audit.all()}
    assert all(answer.audit_id in recorded for answer in (priya, denied, phones, riders, daily))
    assert analyst.audit.verify_chain()[0]


class _RawSqlGenerator:
    def __init__(self, sql: str) -> None:
        self.sql = sql

    def generate(self, user: str, question: str, context: str) -> str:
        return self.sql


class _TrackingConnector(WarehouseConnector):
    """Wraps another connector and records every SQL passed to execute()."""

    def __init__(self, inner: WarehouseConnector) -> None:
        self._inner = inner
        self.executed: list[str] = []

    @property
    def dialect(self) -> str:
        return self._inner.dialect

    def execute(self, sql: str, params=None) -> tuple[list[str], list[tuple]]:
        self.executed.append(sql)
        return self._inner.execute(sql, params)

    def query_history(self) -> list[str]:
        return self._inner.query_history()

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        return self._inner.foreign_keys()


def test_only_firewall_safe_sql_reaches_the_warehouse(tmp_path):
    tracking = _TrackingConnector(DuckDBConnector("data/warehouse.duckdb"))
    analyst = _analyst(tmp_path, connector=tracking)
    # Catalog and mindmap construction queries happened during __init__; only
    # track execute() calls that occur inside ask() from here on.
    tracking.executed.clear()

    analyst.generator = _RawSqlGenerator("SELECT e.salary FROM hr.employees e")
    denied = analyst.ask("gokul", "salary")
    assert denied.decision is Decision.DENY
    assert tracking.executed == []

    raw_sql = "SELECT r.phone FROM rides.riders r"
    analyst.generator = _RawSqlGenerator(raw_sql)
    allowed = analyst.ask("gokul", "phones")
    assert allowed.decision is Decision.MASK
    assert allowed.sql != raw_sql
    assert tracking.executed == [allowed.sql]
