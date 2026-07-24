from pathlib import Path
import sys

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.audit.audit import AuditLog


def _record(log: AuditLog, user: str, tables: list[str]) -> None:
    log.record(
        user=user,
        team="engineering",
        question=f"question from {user}",
        generated_sql="SELECT 1",
        executed_sql="SELECT 1",
        decision="allow",
        reason="permitted",
        tables_touched=tables,
        columns_touched=[],
        masked_columns=[],
        row_count=1,
        latency_ms={"total": 1},
    )


def test_audit_chain_filters_and_tamper_detection(tmp_path):
    path = tmp_path / "audit.duckdb"
    log = AuditLog(str(path))
    _record(log, "gokul", ["rides.trips"])
    _record(log, "priya", ["hr.employees"])
    _record(log, "gokul", ["rides.riders"])

    assert log.verify_chain()[0]
    assert [entry.user for entry in log.for_user("gokul")] == ["gokul", "gokul"]
    assert [entry.user for entry in log.touching_schema("hr")] == ["priya"]

    connection = duckdb.connect(str(path))
    try:
        connection.execute("UPDATE audit_entries SET question = 'altered' WHERE sequence = 2")
    finally:
        connection.close()
    ok, message = log.verify_chain()
    assert not ok
    assert "tampering detected" in message.lower()
