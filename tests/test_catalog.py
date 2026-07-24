from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.catalog.catalog import Catalog
from atlas.policy.engine import PolicyEngine
from atlas.policy.model import ColumnRef, TableRef


DB_PATH = "data/warehouse.duckdb"


def _catalog() -> Catalog:
    return Catalog(DB_PATH, PolicyEngine())


def test_catalog_scopes_gokul_to_rides_only() -> None:
    refs = {table.ref for table in _catalog().tables_for("gokul")}
    assert refs == {
        TableRef("rides", "drivers"),
        TableRef("rides", "locations"),
        TableRef("rides", "riders"),
        TableRef("rides", "trips"),
    }
    assert not any(ref.schema == "hr" for ref in refs)


def test_catalog_scopes_arjun_and_unknown_user_fail_closed() -> None:
    assert {table.ref for table in _catalog().tables_for("arjun")} == {
        TableRef("rides", "trips"),
        TableRef("rides", "locations"),
    }
    assert _catalog().tables_for("mallory") == []


def test_column_stats_never_reads_pii_values() -> None:
    stats = _catalog().column_stats(ColumnRef("rides", "riders", "phone"))
    assert stats == {"masked": True}
    assert not any(key in stats for key in ("samples", "min", "max", "values"))


def test_column_stats_returns_real_non_pii_samples() -> None:
    stats = _catalog().column_stats(ColumnRef("rides", "trips", "status"))
    assert stats["masked"] is False
    assert "completed" in stats["samples"]
    assert stats["min"] == "cancelled"
