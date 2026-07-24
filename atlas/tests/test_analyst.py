from __future__ import annotations

from pathlib import Path
import sys

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.agent.analyst import Analyst
from atlas.agent.generator import DeterministicGenerator
from atlas.policy.model import Decision


def _analyst(tmp_path) -> Analyst:
    return Analyst(
        db_path="data/warehouse.duckdb",
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


def test_only_firewall_safe_sql_reaches_the_warehouse(tmp_path, monkeypatch):
    analyst = _analyst(tmp_path)
    warehouse = analyst.db_path
    real_connect = duckdb.connect
    warehouse_sql: list[str] = []

    class TrackingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args, **kwargs):
            warehouse_sql.append(sql)
            return self.connection.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def tracking_connect(path, *args, **kwargs):
        connection = real_connect(path, *args, **kwargs)
        return TrackingConnection(connection) if str(path) == warehouse else connection

    monkeypatch.setattr("atlas.agent.analyst.duckdb.connect", tracking_connect)

    analyst.generator = _RawSqlGenerator("SELECT e.salary FROM hr.employees e")
    denied = analyst.ask("gokul", "salary")
    assert denied.decision is Decision.DENY
    assert warehouse_sql == []

    raw_sql = "SELECT r.phone FROM rides.riders r"
    analyst.generator = _RawSqlGenerator(raw_sql)
    allowed = analyst.ask("gokul", "phones")
    assert allowed.decision is Decision.MASK
    assert allowed.sql != raw_sql
    assert warehouse_sql == [allowed.sql]
