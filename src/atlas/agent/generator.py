"""SQL generators you can swap out — including one that works fully offline."""

from __future__ import annotations

import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class UnsupportedQuestion(ValueError):
    """Raised when the deterministic demo generator has no safe known mapping."""


class SqlGenerator(Protocol):
    def generate(self, user: str, question: str, context: str) -> str:
        """Return one SQL statement for the question and policy-scoped context."""


class DeterministicGenerator:
    """Offline, dependency-free mappings for the documented demo questions."""

    def generate(self, user: str, question: str, context: str) -> str:
        del user, context  # The firewall, rather than generation, is the security boundary.
        normalized = " ".join(question.lower().replace("→", " to ").replace("-", " ").split())

        if "salary" in normalized and "department" in normalized and ("average" in normalized or "avg" in normalized):
            return """
                SELECT d.name AS department, AVG(e.salary) AS average_salary
                FROM hr.employees e
                JOIN hr.departments d ON e.department_id = d.id
                GROUP BY d.name
                ORDER BY d.name
            """
        if "phone" in normalized and ("rider" in normalized or "riders" in normalized):
            return "SELECT r.phone AS phone FROM rides.riders r"
        if "pan" in normalized and ("employee" in normalized or "employees" in normalized):
            return "SELECT e.pan AS pan FROM hr.employees e"
        if ("average" in normalized or "avg" in normalized) and "rider" in normalized and "airport" in normalized and "downtown" in normalized:
            date_filter = "\n  AND t.trip_date >= CURRENT_DATE - INTERVAL 1 MONTH" if "last month" in normalized else ""
            return f"""
                SELECT AVG(t.rider_count) AS average_riders
                FROM rides.trips t
                JOIN rides.locations s ON t.start_location_id = s.id
                JOIN rides.locations d ON t.end_location_id = d.id
                WHERE s.name = 'Airport' AND d.name = 'Downtown'
                  AND t.status = 'completed'{date_filter}
            """
        if ("trip" in normalized or "trips" in normalized) and "status" in normalized and (
            "how many" in normalized or "per status" in normalized or "by status" in normalized
        ):
            return """
                SELECT t.status AS status, COUNT(*) AS trip_count
                FROM rides.trips t
                GROUP BY t.status
                ORDER BY t.status
            """
        if ("top driver" in normalized or "busiest driver" in normalized):
            return """
                SELECT d.id AS driver_id, COUNT(*) AS trip_count
                FROM rides.trips t
                JOIN rides.drivers d ON t.driver_id = d.id
                GROUP BY d.id
                ORDER BY trip_count DESC
                LIMIT 10
            """
        if ("trips per day" in normalized or "daily trips" in normalized):
            return """
                SELECT t.trip_date AS trip_date, COUNT(*) AS trip_count
                FROM rides.trips t
                GROUP BY t.trip_date
                ORDER BY t.trip_date
            """
        raise UnsupportedQuestion(
            "I can answer the documented rides and HR demo questions, but I don't yet recognize that question."
        )


class ClaudeGenerator:
    """Minimal Anthropic Messages API client, used only when configured."""

    endpoint = "https://api.anthropic.com/v1/messages"
    model = "claude-sonnet-5"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required to use ClaudeGenerator.")

    def generate(self, user: str, question: str, context: str) -> str:
        del user
        system = (
            "You generate DuckDB SQL. Use ONLY tables and columns in the provided context. "
            "Always schema-qualify tables. Emit exactly one read-only SELECT statement. "
            "Return SQL only: no prose and no Markdown fences."
        )
        body = json.dumps({
            "model": self.model,
            "max_tokens": 600,
            "system": system,
            "messages": [{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}],
        }).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return str(payload["content"][0]["text"]).strip()
        except (HTTPError, URLError, KeyError, IndexError, json.JSONDecodeError) as error:
            raise RuntimeError("The configured SQL generator could not produce SQL.") from error


def default_generator() -> SqlGenerator:
    return ClaudeGenerator() if os.environ.get("ANTHROPIC_API_KEY") else DeterministicGenerator()
