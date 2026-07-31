"""WS4 governance tests: YAML policy loader + row-level security."""

from __future__ import annotations

import os
import sys
from pathlib import Path
import textwrap

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _reset_policy_env():
    """Clear ATLAS_POLICY_FILE and the singleton before and after each test."""
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


def _write_policy(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(textwrap.dedent(body).lstrip("\n"))
    return path


def test_yaml_loader_reflects_disk_config(tmp_path: Path):
    from atlas.policy import loader as loader_mod
    loader_mod.reset_policy_loader_for_tests()

    policy_path = _write_policy(tmp_path, """
        tables:
          rides:
            trips: [id, driver_id, status]
        pii_columns: []
        users:
          alice:
            team: analytics
            visible_tables: [rides.trips]
            unmasked_pii: []
    """)
    os.environ["ATLAS_POLICY_FILE"] = str(policy_path)

    from atlas.policy.engine import PolicyEngine
    from atlas.policy.model import ColumnRef, TableRef

    engine = PolicyEngine()
    assert "alice" in engine.users()
    visible = engine.visible_tables("alice")
    assert TableRef("rides", "trips") in visible

    # Bob unknown → empty visibility.
    assert engine.visible_tables("bob") == set()


def test_yaml_hot_reload(tmp_path: Path):
    from atlas.policy import loader as loader_mod
    loader_mod.reset_policy_loader_for_tests()

    policy_path = _write_policy(tmp_path, """
        tables:
          rides:
            trips: [id, driver_id]
        users:
          alice:
            team: analytics
            visible_tables: [rides.trips]
    """)
    os.environ["ATLAS_POLICY_FILE"] = str(policy_path)

    from atlas.policy.engine import PolicyEngine
    engine = PolicyEngine()
    assert "alice" in engine.users()

    # Rewrite with a new user.
    import time
    time.sleep(0.05)
    _write_policy(tmp_path, """
        tables:
          rides:
            trips: [id, driver_id]
        users:
          alice:
            team: analytics
            visible_tables: [rides.trips]
          bob:
            team: ops
            visible_tables: [rides.trips]
    """)
    os.utime(policy_path, None)  # bump mtime
    assert "bob" in engine.users()


def test_row_level_predicate_injected(tmp_path: Path):
    from atlas.policy import loader as loader_mod
    loader_mod.reset_policy_loader_for_tests()

    policy_path = _write_policy(tmp_path, """
        tables:
          rides:
            trips: [id, driver_id, status]
        pii_columns: []
        users:
          alice:
            team: analytics
            visible_tables: [rides.trips]
            unmasked_pii: []
            row_predicates:
              rides.trips: "status = 'completed'"
    """)
    os.environ["ATLAS_POLICY_FILE"] = str(policy_path)

    from atlas.firewall.firewall import SqlFirewall
    from atlas.policy.engine import PolicyEngine

    firewall = SqlFirewall(PolicyEngine(), dialect="duckdb")
    result = firewall.check("alice", "SELECT t.id FROM rides.trips t")
    assert result.decision.value == "allow"
    # The predicate must appear in the final safe SQL.
    assert "completed" in (result.safe_sql or "").lower()


def test_row_level_predicate_absent_for_user_without_rule(tmp_path: Path):
    from atlas.policy import loader as loader_mod
    loader_mod.reset_policy_loader_for_tests()

    policy_path = _write_policy(tmp_path, """
        tables:
          rides:
            trips: [id, driver_id, status]
        users:
          alice:
            team: analytics
            visible_tables: [rides.trips]
            row_predicates:
              rides.trips: "status = 'completed'"
          bob:
            team: analytics
            visible_tables: [rides.trips]
    """)
    os.environ["ATLAS_POLICY_FILE"] = str(policy_path)

    from atlas.firewall.firewall import SqlFirewall
    from atlas.policy.engine import PolicyEngine

    firewall = SqlFirewall(PolicyEngine(), dialect="duckdb")
    result = firewall.check("bob", "SELECT t.id FROM rides.trips t")
    assert result.decision.value == "allow"
    assert "completed" not in (result.safe_sql or "").lower()


def test_malformed_predicate_denies(tmp_path: Path):
    from atlas.policy import loader as loader_mod
    loader_mod.reset_policy_loader_for_tests()

    policy_path = _write_policy(tmp_path, """
        tables:
          rides:
            trips: [id, driver_id, status]
        users:
          alice:
            team: analytics
            visible_tables: [rides.trips]
            row_predicates:
              rides.trips: "this is not a valid SQL predicate"
    """)
    os.environ["ATLAS_POLICY_FILE"] = str(policy_path)

    from atlas.firewall.firewall import SqlFirewall
    from atlas.policy.engine import PolicyEngine

    firewall = SqlFirewall(PolicyEngine(), dialect="duckdb")
    result = firewall.check("alice", "SELECT t.id FROM rides.trips t")
    assert result.decision.value == "deny"
