"""WS5 tests: semantic metrics, dashboard, few-shot library."""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _reset_env():
    from atlas.policy import loader as loader_mod
    from atlas.semantic import reset_registry_for_tests
    prev_pol = os.environ.pop("ATLAS_POLICY_FILE", None)
    prev_met = os.environ.pop("ATLAS_METRICS_FILE", None)
    loader_mod.reset_policy_loader_for_tests()
    reset_registry_for_tests()
    try:
        yield
    finally:
        os.environ.pop("ATLAS_POLICY_FILE", None)
        os.environ.pop("ATLAS_METRICS_FILE", None)
        loader_mod.reset_policy_loader_for_tests()
        reset_registry_for_tests()
        if prev_pol is not None: os.environ["ATLAS_POLICY_FILE"] = prev_pol
        if prev_met is not None: os.environ["ATLAS_METRICS_FILE"] = prev_met


def test_semantic_metric_beats_llm(tmp_path: Path):
    """A curated metric that matches the question should be used directly."""
    from atlas.data.generate import generate
    from atlas.agent.analyst import Analyst
    from atlas.connector import DuckDBConnector

    prev = Path.cwd()
    os.chdir(tmp_path)
    try:
        generate()
        metrics_path = tmp_path / "metrics.yaml"
        metrics_path.write_text(textwrap.dedent("""
            metrics:
              trips_per_status:
                description: "Number of trips by status."
                requires_tables: [rides.trips]
                sql: |
                  SELECT 'completed' AS status, 999 AS trip_count
        """).lstrip())
        os.environ["ATLAS_METRICS_FILE"] = str(metrics_path)

        analyst = Analyst(
            connector=DuckDBConnector(str(tmp_path / "data" / "warehouse.duckdb")),
            audit_path=str(tmp_path / "audit.duckdb"),
        )
        answer = analyst.ask("gokul", "trips per status")
        assert answer.decision.value == "allow"
        # The metric's hardcoded 999 must show up — proving metric was used.
        assert any(999 in row for row in answer.rows), answer.rows
    finally:
        os.chdir(prev)


def test_semantic_no_match_falls_back_to_generator(tmp_path: Path):
    from atlas.data.generate import generate
    from atlas.agent.analyst import Analyst
    from atlas.connector import DuckDBConnector

    prev = Path.cwd()
    os.chdir(tmp_path)
    try:
        generate()
        metrics_path = tmp_path / "metrics.yaml"
        metrics_path.write_text("metrics: {}\n")
        os.environ["ATLAS_METRICS_FILE"] = str(metrics_path)

        analyst = Analyst(
            connector=DuckDBConnector(str(tmp_path / "data" / "warehouse.duckdb")),
            audit_path=str(tmp_path / "audit.duckdb"),
        )
        # 'trips per status' hits the DeterministicGenerator branch.
        answer = analyst.ask("gokul", "trips per status")
        assert answer.decision.value == "allow"
        assert answer.rows
    finally:
        os.chdir(prev)


def test_fewshot_returns_only_user_examples(tmp_path: Path):
    from atlas.audit.audit import AuditLog
    from atlas.agent.fewshot import FewShotLibrary

    audit = AuditLog(str(tmp_path / "audit.duckdb"))
    audit.record(user="alice", team="analytics", question="q1",
                 generated_sql=None, executed_sql="SELECT 1", decision="allow",
                 reason="ok", tables_touched=[], columns_touched=[],
                 masked_columns=[], row_count=1, latency_ms={})
    audit.record(user="bob", team="hr", question="q2",
                 generated_sql=None, executed_sql="SELECT 2", decision="allow",
                 reason="ok", tables_touched=[], columns_touched=[],
                 masked_columns=[], row_count=1, latency_ms={})
    audit.record(user="alice", team="analytics", question="q3",
                 generated_sql=None, executed_sql=None, decision="deny",
                 reason="no", tables_touched=[], columns_touched=[],
                 masked_columns=[], row_count=None, latency_ms={})

    lib = FewShotLibrary(audit)
    examples = lib.for_user("alice")
    assert len(examples) == 1
    assert examples[0].sql == "SELECT 1"


def test_dashboard_computes_totals(tmp_path: Path):
    """The dashboard route summarises audit entries correctly."""
    os.environ["ATLAS_AUTH_MODE"] = "disabled"
    for mod in ["atlas.api.app", "atlas.config.settings", "atlas.config.secrets"]:
        sys.modules.pop(mod, None)
    from atlas.config import secrets as sm, settings as st
    sm.reset_secrets_for_tests()
    st.reset_settings_for_tests()

    from atlas.api.app import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    # Perform a couple asks to seed the audit.
    client.post("/api/ask", json={"user": "gokul", "question": "top drivers"})
    client.post("/api/ask", json={"user": "gokul", "question": "average salary by department"})
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    payload = r.json()
    assert payload["total_decisions"] >= 2
    assert "decision_counts" in payload
    assert "top_users" in payload
