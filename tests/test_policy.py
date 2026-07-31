from pathlib import Path
import sys


# This repository intentionally has no packaging metadata yet; test source in place.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.policy.engine import PolicyEngine
from atlas.policy.model import ColumnRef, Decision, TableRef


def test_gokul_catalog_hides_hr_tables() -> None:
    engine = PolicyEngine()
    assert all(table.schema != "hr" for table in engine.visible_tables("gokul"))


def test_gokul_is_denied_hr_with_explanation() -> None:
    verdict = PolicyEngine().check_table("gokul", TableRef("hr", "employees"))
    assert verdict.decision is Decision.DENY
    assert "engineering" in verdict.reason.lower()


def test_gokul_rider_phone_is_masked() -> None:
    verdict = PolicyEngine().check_column("gokul", ColumnRef("rides", "riders", "phone"))
    assert verdict.decision is Decision.MASK


def test_arjun_cannot_access_riders() -> None:
    verdict = PolicyEngine().check_table("arjun", TableRef("rides", "riders"))
    assert verdict.decision is Decision.DENY


def test_mitra_masks_pan_but_can_access_salary() -> None:
    engine = PolicyEngine()
    assert engine.check_column("mitra", ColumnRef("hr", "employees", "pan")).decision is Decision.MASK
    assert engine.check_column("mitra", ColumnRef("hr", "employees", "salary")).decision is Decision.ALLOW


def test_unknown_user_fails_closed() -> None:
    engine = PolicyEngine()
    assert engine.visible_tables("mallory") == set()
    assert engine.check_table("mallory", TableRef("rides", "trips")).decision is Decision.DENY
    assert engine.check_column("mallory", ColumnRef("rides", "trips", "fare_amount")).decision is Decision.DENY


def test_auditor_sees_no_business_data_tables() -> None:
    engine = PolicyEngine()
    assert engine.visible_tables("auditor") == set()
    assert engine.check_table("auditor", TableRef("rides", "trips")).decision is Decision.DENY
    assert engine.check_table("auditor", TableRef("hr", "employees")).decision is Decision.DENY


def test_denial_reason_is_not_an_enumeration_oracle() -> None:
    """A refusal must not reveal whether the table actually exists (README 5B).

    If 'hr.employees' and a made-up 'hr.unicorn_salaries' produce different wording,
    an attacker can enumerate the real HR schema one probe at a time.
    """
    engine = PolicyEngine()
    real = engine.check_table("gokul", TableRef("hr", "employees"))
    fake = engine.check_table("gokul", TableRef("hr", "unicorn_salaries"))
    assert real.decision is Decision.DENY
    assert fake.decision is Decision.DENY
    assert real.reason == fake.reason, "denial wording leaks table existence"


def test_denial_oracle_closed_for_visible_schema_too() -> None:
    """Same property inside a schema the user CAN see: rides.trips is visible,
    but a non-existent rides table must not be distinguishable from a forbidden one."""
    engine = PolicyEngine()
    forbidden = engine.check_table("arjun", TableRef("rides", "riders"))   # exists, not visible to arjun
    missing = engine.check_table("arjun", TableRef("rides", "made_up_tbl"))  # does not exist
    assert forbidden.decision is Decision.DENY
    assert missing.decision is Decision.DENY
    assert forbidden.reason == missing.reason, "denial wording leaks table existence"
