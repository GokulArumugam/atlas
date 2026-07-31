"""Adversarial tests for the SQL firewall security boundary."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.firewall.firewall import SqlFirewall
from atlas.policy.engine import PolicyEngine
from atlas.policy.model import ColumnRef, Decision


DEMO_SQL = """
SELECT s.name AS start_location, d.name AS end_location,
       AVG(t.rider_count) AS average_riders
FROM rides.trips t
JOIN rides.locations s ON t.start_location_id = s.id
JOIN rides.locations d ON t.end_location_id = d.id
WHERE s.name = 'Airport' AND d.name = 'Downtown'
GROUP BY s.name, d.name
"""

ALLOW_CASES = [
    ("gokul", "SELECT AVG(rider_count) FROM rides.trips"),
    ("gokul", "SELECT * FROM rides.trips"),
    ("gokul", "SELECT COUNT(phone) FROM rides.riders"),
    ("mitra", "SELECT AVG(salary) FROM hr.employees"),
    ("gokul", DEMO_SQL),
    ("gokul", "SELECT 1"),
    ("gokul", "WITH x AS (SELECT phone FROM rides.riders) SELECT COUNT(phone) FROM x"),
    ("gokul", "SELECT id FROM rides.trips UNION SELECT id FROM rides.trips"),
]

MASK_CASES = [
    ("gokul", "SELECT phone FROM rides.riders"),
    ("gokul", "SELECT * FROM rides.riders"),
    ("gokul", "SELECT phone AS p FROM rides.riders"),
    ("mitra", "SELECT pan FROM hr.employees"),
    ("gokul", "SELECT x.phone FROM (SELECT phone FROM rides.riders) x"),
]


@pytest.fixture
def firewall() -> SqlFirewall:
    return SqlFirewall(PolicyEngine())


@pytest.mark.parametrize(("user", "sql"), ALLOW_CASES)
def test_allows_safe_read_queries(firewall: SqlFirewall, user: str, sql: str) -> None:
    result = firewall.check(user, sql)
    assert result.decision is Decision.ALLOW
    assert result.safe_sql is not None


def test_star_expands_and_tracks_non_pii_columns(firewall: SqlFirewall) -> None:
    result = firewall.check("gokul", "SELECT * FROM rides.trips")
    assert "*" not in result.safe_sql
    assert ColumnRef("rides", "trips", "rider_count") in result.columns_touched


@pytest.mark.parametrize(("user", "sql"), MASK_CASES)
def test_masks_pii_in_output_projections(firewall: SqlFirewall, user: str, sql: str) -> None:
    result = firewall.check(user, sql)
    assert result.decision is Decision.MASK
    assert result.safe_sql is not None
    assert "***MASKED***" in result.safe_sql
    assert result.masked_columns


def test_masking_preserves_output_alias(firewall: SqlFirewall) -> None:
    result = firewall.check("gokul", "SELECT phone AS p FROM rides.riders")
    assert result.decision is Decision.MASK
    assert "AS p" in result.safe_sql


@pytest.mark.parametrize(
    "user, sql",
    [
        ("gokul", "SELECT * FROM hr.employees"),
        ("gokul", "WITH x AS (SELECT * FROM hr.employees) SELECT * FROM x"),
        (
            "gokul",
            "WITH a AS (SELECT * FROM hr.employees), b AS (SELECT * FROM a) SELECT * FROM b",
        ),
        ("gokul", "SELECT (SELECT MAX(salary) FROM hr.employees) FROM rides.trips"),
        (
            "gokul",
            "SELECT * FROM rides.trips WHERE rider_id IN (SELECT id FROM hr.employees)",
        ),
        ("gokul", "SELECT id FROM rides.trips UNION SELECT id FROM hr.employees"),
        ("gokul", "SELECT * FROM rides.trips CROSS JOIN hr.employees"),
        ("gokul", 'SELECT * FROM "hr"."employees"'),
        ("gokul", "SELECT * FROM HR.EMPLOYEES"),
        ("gokul", "SELECT /* x */ * FROM hr.employees"),
        ("arjun", "SELECT * FROM rides.riders"),
        ("mallory", "SELECT * FROM rides.trips"),
        ("gokul", "SELECT phone FROM rides.riders WHERE phone = '9876543210'"),
        ("gokul", "SELECT phone FROM rides.riders ORDER BY phone"),
        ("gokul", "SELECT phone FROM rides.riders GROUP BY phone"),
        ("gokul", "INSERT INTO rides.trips VALUES (1, 1, 1, 1, 2, CURRENT_DATE, 1, 1, 'x')"),
        ("gokul", "UPDATE rides.trips SET status = 'x'"),
        ("gokul", "DELETE FROM rides.trips"),
        ("gokul", "DROP TABLE rides.trips"),
        ("gokul", "CREATE TABLE nope (id INT)"),
        ("gokul", "ALTER TABLE rides.trips ADD COLUMN nope INT"),
        ("gokul", "TRUNCATE TABLE rides.trips"),
        ("gokul", "SELECT 1; DROP TABLE rides.trips"),
        ("gokul", "this is not SQL !!!"),
        ("gokul", ""),
        (
            "gokul",
            "SELECT id FROM rides.trips t JOIN rides.riders r ON t.rider_id = r.id",
        ),
    ],
)
def test_denies_adversarial_or_unsafe_sql(firewall: SqlFirewall, user: str, sql: str) -> None:
    result = firewall.check(user, sql)
    assert result.decision is Decision.DENY
    assert result.safe_sql is None


def test_table_denial_is_not_an_enumeration_oracle_through_firewall(firewall: SqlFirewall) -> None:
    real = firewall.check("gokul", "SELECT * FROM hr.employees")
    fake = firewall.check("gokul", "SELECT * FROM hr.unicorn_salaries")
    assert real.decision is Decision.DENY
    assert fake.decision is Decision.DENY
    assert real.reason == fake.reason


def test_every_denial_has_no_executable_sql(firewall: SqlFirewall) -> None:
    attempts = [
        "SELECT * FROM hr.employees",
        "SELECT phone FROM rides.riders WHERE phone = '9876543210'",
        "SELECT 1; DROP TABLE rides.trips",
        "UPDATE rides.trips SET status = 'x'",
        "not sql",
        "",
    ]
    for sql in attempts:
        result = firewall.check("gokul", sql)
        assert result.decision is Decision.DENY
        assert result.safe_sql is None


def test_refusal_names_the_actual_column_not_a_canned_example() -> None:
    """A refusal about PAN must not say "phone numbers".

    The wording has to follow the offending column; a hard-coded example makes the
    firewall look canned and misinforms the user about what was actually blocked.
    """
    firewall = SqlFirewall(PolicyEngine())
    pan = firewall.check("mitra", "SELECT id FROM hr.employees WHERE pan = 'ABCDE1234F'")
    email = firewall.check("gokul", "SELECT id FROM rides.riders WHERE email = 'a@b.com'")
    name = firewall.check("gokul", "SELECT id FROM rides.riders ORDER BY full_name")

    assert pan.decision is Decision.DENY and "PAN" in pan.reason
    assert "Phone" not in pan.reason
    assert email.decision is Decision.DENY and "Email" in email.reason
    assert name.decision is Decision.DENY and "Full names" in name.reason
