from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.catalog.catalog import Catalog
from atlas.catalog.context import build_context
from atlas.catalog.mindmap import MindMap
from atlas.policy.engine import PolicyEngine
from atlas.policy.model import ColumnRef, TableRef


DB_PATH = "data/warehouse.duckdb"


def _map() -> tuple[Catalog, MindMap]:
    catalog = Catalog(DB_PATH, PolicyEngine())
    mindmap = MindMap(catalog)
    mindmap.build(DB_PATH)
    return catalog, mindmap


def _has_edge(edges, left: ColumnRef, right: ColumnRef) -> bool:
    return any({edge.left, edge.right} == {left, right} for edge in edges)


def test_history_mines_location_and_rider_joins() -> None:
    _, mindmap = _map()
    edges = mindmap.edges_for("gokul")
    assert _has_edge(
        edges,
        ColumnRef("rides", "trips", "start_location_id"),
        ColumnRef("rides", "locations", "id"),
    )
    assert _has_edge(
        edges,
        ColumnRef("rides", "trips", "rider_id"),
        ColumnRef("rides", "riders", "id"),
    )
    location_edge = next(edge for edge in edges if edge.left.column == "start_location_id")
    assert "historical queries" in location_edge.evidence


def test_mindmap_scopes_edges_and_mermaid() -> None:
    _, mindmap = _map()
    gokul_edges = mindmap.edges_for("gokul")
    assert all(edge.left.schema != "hr" and edge.right.schema != "hr" for edge in gokul_edges)
    arjun_edges = mindmap.edges_for("arjun")
    assert all(edge.left.table != "riders" and edge.right.table != "riders" for edge in arjun_edges)

    mermaid = mindmap.to_mermaid("gokul")
    assert "rides.trips" in mermaid
    assert "rides.locations" in mermaid
    assert "hr." not in mermaid


def test_context_is_relevant_and_cannot_leak_forbidden_tables() -> None:
    catalog, mindmap = _map()
    gokul_context = build_context(
        "gokul", "average riders from Airport to Downtown", catalog, mindmap
    )
    assert "rides.trips" in gokul_context
    assert "rides.locations" in gokul_context
    assert "start_location_id = rides.locations.id" in gokul_context
    assert "hr." not in gokul_context

    arjun_context = build_context("arjun", "riders travelling from Airport", catalog, mindmap)
    assert "TABLE rides.riders" not in arjun_context
    assert "riders" not in arjun_context.lower()
