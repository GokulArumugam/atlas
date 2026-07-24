"""Execute every allow/mask example against the real read-only DuckDB warehouse."""

from __future__ import annotations

from pathlib import Path
import sys

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.firewall.firewall import SqlFirewall
from atlas.policy.engine import PolicyEngine
from atlas.policy.model import Decision
from test_firewall import ALLOW_CASES, MASK_CASES


@pytest.mark.parametrize(("user", "sql"), ALLOW_CASES + MASK_CASES)
def test_allowed_and_masked_sql_executes_against_warehouse(user: str, sql: str) -> None:
    result = SqlFirewall(PolicyEngine()).check(user, sql)
    assert result.decision in (Decision.ALLOW, Decision.MASK)
    assert result.safe_sql is not None

    database = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"
    connection = duckdb.connect(str(database), read_only=True)
    try:
        rows = connection.execute(result.safe_sql).fetchall()
    finally:
        connection.close()
    assert rows
