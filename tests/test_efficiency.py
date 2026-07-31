"""WS2 efficiency tests: SQL cache, audit SQL pushdown, connection reuse."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_sql_cache_hits_on_repeat(tmp_path: Path):
    """Second identical ask under the same identity should hit the SQL cache."""
    from atlas.agent.analyst import Analyst
    from atlas.connector import DuckDBConnector

    warehouse = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"
    if not warehouse.exists():
        # Fall back to generating a temporary warehouse.
        from atlas.data.generate import generate
        import os
        os.chdir(tmp_path)
        generate()
        warehouse = tmp_path / "data" / "warehouse.duckdb"

    analyst = Analyst(
        connector=DuckDBConnector(str(warehouse)),
        audit_path=str(tmp_path / "audit.duckdb"),
    )
    a = analyst.ask("gokul", "top drivers")
    b = analyst.ask("gokul", "top drivers")
    stats = analyst.cache_stats()
    assert stats["hits"] >= 1, stats
    assert a.sql == b.sql


def test_touching_schema_sql_pushdown(tmp_path: Path):
    """touching_schema must filter in SQL, not by loading all rows into Python."""
    from atlas.audit.audit import AuditLog

    audit = AuditLog(str(tmp_path / "audit.duckdb"))
    audit.record(
        user="gokul", team="engineering", question="q1",
        generated_sql=None, executed_sql="SELECT * FROM rides.trips",
        decision="allow", reason="ok",
        tables_touched=["rides.trips"], columns_touched=["rides.trips.id"],
        masked_columns=[], row_count=1, latency_ms={},
    )
    audit.record(
        user="mitra", team="hr", question="q2",
        generated_sql=None, executed_sql="SELECT * FROM hr.employees",
        decision="allow", reason="ok",
        tables_touched=["hr.employees"], columns_touched=["hr.employees.salary"],
        masked_columns=[], row_count=1, latency_ms={},
    )
    rides = audit.touching_schema("rides")
    hr = audit.touching_schema("hr")
    assert len(rides) == 1 and rides[0].question == "q1"
    assert len(hr) == 1 and hr[0].question == "q2"


def test_duckdb_connector_reuses_connection(tmp_path: Path):
    """Two executes must share the same underlying connection."""
    from atlas.connector import DuckDBConnector
    from atlas.data.generate import generate
    import os
    prev = Path.cwd()
    os.chdir(tmp_path)
    try:
        generate()
        conn = DuckDBConnector(str(tmp_path / "data" / "warehouse.duckdb"))
        conn.execute("SELECT 1")
        first = conn._conn
        conn.execute("SELECT 2")
        second = conn._conn
        assert first is second
    finally:
        os.chdir(prev)
