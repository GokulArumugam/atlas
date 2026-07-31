"""WS4.3 + WS4.5 tests: cost estimator + WORM audit sink."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _reset_policy_env():
    from atlas.policy import loader as loader_mod
    prev = os.environ.pop("ATLAS_POLICY_FILE", None)
    loader_mod.reset_policy_loader_for_tests()
    try:
        yield
    finally:
        os.environ.pop("ATLAS_POLICY_FILE", None)
        loader_mod.reset_policy_loader_for_tests()
        if prev is not None:
            os.environ["ATLAS_POLICY_FILE"] = prev


def test_worm_sink_writes_read_only_file(tmp_path: Path):
    from atlas.audit.worm import WORMSink
    from atlas.audit.audit import AuditEntry

    sink = WORMSink(tmp_path)
    entry = AuditEntry(
        audit_id="abc123", ts="2020-01-01T00:00:00Z", user="alice", team="analytics",
        question="q", generated_sql="sel", executed_sql="sel",
        decision="allow", reason="ok",
        tables_touched=["rides.trips"], columns_touched=["rides.trips.id"],
        masked_columns=[], row_count=1, latency_ms={},
        prev_hash="0", entry_hash="h",
    )
    path = sink.write(entry, sequence=1)
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["audit_id"] == "abc123"

    # File is read-only.
    mode = path.stat().st_mode & 0o777
    assert mode == 0o444, oct(mode)

    # Rewriting the same sequence + audit_id must fail (O_EXCL).
    entry_same = AuditEntry(**payload)
    with pytest.raises(FileExistsError):
        sink.write(entry_same, sequence=1)


def test_cost_estimator_denies_over_limit(tmp_path: Path):
    """A per-user cost limit rejects queries whose EC exceeds it."""
    # Use a hardcoded policy config to avoid the loader machinery.
    from atlas.agent.analyst import Analyst
    from atlas.connector import DuckDBConnector
    from atlas.data.generate import generate

    prev = Path.cwd()
    os.chdir(tmp_path)
    try:
        generate()
        # Override via file-backed loader with a tiny cost limit.
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(textwrap.dedent("""
            tables:
              rides:
                trips: [id, rider_id, driver_id, start_location_id, end_location_id, trip_date, rider_count, fare_amount, status]
            pii_columns: []
            users:
              analyst:
                team: analytics
                visible_tables: [rides.trips]
                unmasked_pii: []
                cost_limits:
                  max_result_rows: 5
        """).lstrip())
        os.environ["ATLAS_POLICY_FILE"] = str(policy_path)

        from atlas.policy import loader as loader_mod
        loader_mod.reset_policy_loader_for_tests()

        analyst = Analyst(
            connector=DuckDBConnector(str(tmp_path / "data" / "warehouse.duckdb")),
            audit_path=str(tmp_path / "audit.duckdb"),
        )
        # 'trips per status' has an EC well above 5.
        answer = analyst.ask("analyst", "trips per status")
        assert answer.decision.value == "deny", answer
        assert "exceeds" in answer.reason.lower() or "limit" in answer.reason.lower()
    finally:
        os.chdir(prev)


def test_cost_estimator_allows_when_within_limit(tmp_path: Path):
    from atlas.agent.analyst import Analyst
    from atlas.connector import DuckDBConnector
    from atlas.data.generate import generate

    prev = Path.cwd()
    os.chdir(tmp_path)
    try:
        generate()
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(textwrap.dedent("""
            tables:
              rides:
                trips: [id, status]
            pii_columns: []
            users:
              analyst:
                team: analytics
                visible_tables: [rides.trips]
                cost_limits:
                  max_result_rows: 1000000
        """).lstrip())
        os.environ["ATLAS_POLICY_FILE"] = str(policy_path)

        from atlas.policy import loader as loader_mod
        loader_mod.reset_policy_loader_for_tests()

        analyst = Analyst(
            connector=DuckDBConnector(str(tmp_path / "data" / "warehouse.duckdb")),
            audit_path=str(tmp_path / "audit.duckdb"),
        )
        answer = analyst.ask("analyst", "trips per status")
        assert answer.decision.value == "allow", answer
    finally:
        os.chdir(prev)


def test_worm_env_enables_mirror(tmp_path: Path):
    """When ATLAS_WORM_AUDIT_DIR is set, every recorded entry mirrors to disk."""
    os.environ["ATLAS_WORM_AUDIT_DIR"] = str(tmp_path / "worm")
    try:
        from atlas.audit.worm import reset_worm_for_tests
        reset_worm_for_tests()

        from atlas.audit.audit import AuditLog
        audit = AuditLog(str(tmp_path / "audit.duckdb"))
        audit.record(
            user="alice", team="analytics", question="q1",
            generated_sql=None, executed_sql="SELECT 1",
            decision="allow", reason="ok",
            tables_touched=["rides.trips"], columns_touched=[], masked_columns=[],
            row_count=0, latency_ms={},
        )
        files = list((tmp_path / "worm").glob("*.jsonl"))
        assert len(files) == 1
        entry = json.loads(files[0].read_text())
        assert entry["user"] == "alice"
    finally:
        os.environ.pop("ATLAS_WORM_AUDIT_DIR", None)
        from atlas.audit.worm import reset_worm_for_tests
        reset_worm_for_tests()
