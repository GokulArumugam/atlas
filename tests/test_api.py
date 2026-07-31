"""End-to-end checks for the thin HTTP facade around the governed runtime."""

from __future__ import annotations

from pathlib import Path
import re
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.api.app import app


client = TestClient(app)


def test_users_expose_the_four_demo_identities() -> None:
    response = client.get("/api/users")
    assert response.status_code == 200
    assert response.json() == [
        {"user": "gokul", "team": "engineering"},
        {"user": "mitra", "team": "hr"},
        {"user": "arjun", "team": "marketing"},
        {"user": "auditor", "team": "audit"},
    ]


def test_engineering_cannot_access_hr_salary_data() -> None:
    response = client.post("/api/ask", json={"user": "gokul", "question": "average salary by department"})
    answer = response.json()
    assert response.status_code == 200
    assert answer["decision"] == "deny"
    assert answer["rows"] == [] and answer["sql"] is None
    assert "engineering" in answer["reason"].lower()


def test_hr_can_access_average_salary_data() -> None:
    response = client.post("/api/ask", json={"user": "mitra", "question": "average salary by department"})
    assert response.status_code == 200
    answer = response.json()
    assert answer["decision"] == "allow"
    assert answer["rows"]


def test_phone_values_are_masked_before_they_reach_the_api() -> None:
    response = client.post("/api/ask", json={"user": "gokul", "question": "show me riders' phone numbers"})
    answer = response.json()
    assert response.status_code == 200
    assert answer["decision"] == "mask"
    assert all(value == "***MASKED***" for row in answer["rows"] for value in row)
    assert not any(re.search(r"\d{6,}", str(value)) for row in answer["rows"] for value in row)


def test_airport_to_downtown_average_is_returned() -> None:
    response = client.post(
        "/api/ask", json={"user": "gokul", "question": "average riders from Airport to Downtown"}
    )
    answer = response.json()
    assert response.status_code == 200
    assert answer["decision"] == "allow"
    assert abs(answer["rows"][0][0] - 2.5) < 0.1


def test_graph_is_scoped_for_gokul() -> None:
    response = client.get("/api/graph/gokul")
    assert response.status_code == 200
    assert "hr." not in response.text.lower()


def test_graph_includes_hr_for_mitra() -> None:
    response = client.get("/api/graph/mitra")
    assert response.status_code == 200
    assert "hr." in response.text.lower()


def test_audit_is_verified_and_contains_denied_interaction() -> None:
    response = client.get("/api/audit")
    payload = response.json()
    assert response.status_code == 200
    assert payload["chain_ok"]
    assert payload["entries"]
    assert any(
        entry["question"] == "average salary by department" and entry["decision"] == "deny"
        for entry in payload["entries"]
    )


def test_index_serves_the_single_page_interface() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Governed AI Data Analyst" in response.text
