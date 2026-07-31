"""A small, defensive FastAPI wrapper around the analyst."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import Request

from atlas.agent.analyst import Analyst, AnalystAnswer
from atlas.audit.audit import AuditEntry
from atlas.connector import DuckDBConnector


ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = ROOT / "static"
DB_PATH = ROOT / "data" / "warehouse.duckdb"
AUDIT_PATH = ROOT / "data" / "audit.duckdb"

# This object owns a Catalog and MindMap built once when the application module is
# loaded.  Request handlers only use its already-scoped views, avoiding a costly
# re-parse of the historical query corpus for every browser interaction.
analyst = Analyst(connector=DuckDBConnector(str(DB_PATH)), audit_path=str(AUDIT_PATH))

app = FastAPI(
    title="Atlas — Governed AI Data Analyst",
    version="1.0",
    description=(
        "⚠️ DEMO ONLY — there is no authentication here. The API trusts whatever "
        "`user` you send it, so don't point this at a real warehouse. The firewall, "
        "per-user scoping, and audit trail are real; the auth layer is intentionally "
        "left as 'later.'"
    ),
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class AskRequest(BaseModel):
    user: str
    question: str


def _error(message: str, status_code: int = 500) -> JSONResponse:
    """Return a deliberately plain error response, never an exception detail."""
    return JSONResponse(status_code=status_code, content={"error": message})


def _answer_payload(answer: AnalystAnswer) -> dict[str, Any]:
    return {
        "decision": answer.decision.value,
        "reason": answer.reason,
        "question": answer.question,
        "sql": answer.sql,
        "columns": answer.columns,
        "rows": [list(row) for row in answer.rows],
        "chart": answer.chart,
        "tables_touched": answer.tables_touched,
        "masked_columns": answer.masked_columns,
        "latency_ms": answer.latency_ms,
        "audit_id": answer.audit_id,
    }


def _audit_payload(entry: AuditEntry) -> dict[str, Any]:
    """Convert dataclass entries while retaining useful audit-console detail."""
    return asdict(entry)


@app.exception_handler(Exception)
async def _unexpected_error(_request: Request, _error_value: Exception) -> JSONResponse:
    return _error("The service encountered an unexpected error. Please try again.")


@app.get("/api/users")
def users() -> JSONResponse:
    try:
        return JSONResponse(content=[
            {"user": user, "team": analyst.policy.team_of(user)}
            for user in analyst.policy.users()
        ])
    except Exception:
        return _error("Unable to load users right now.")


@app.post("/api/ask")
def ask(request: AskRequest) -> JSONResponse:
    try:
        user = request.user.strip()
        question = request.question.strip()
        if not user or not question:
            return _error("Both user and question are required.", 400)
        answer = analyst.ask(user, question)
        return JSONResponse(content=jsonable_encoder(_answer_payload(answer)))
    except Exception:
        return _error("Unable to complete that governed query.")


@app.get("/api/graph/{user}")
def graph(user: str) -> JSONResponse:
    try:
        tables = analyst.catalog.tables_for(user)
        nodes = [
            {"id": str(table.ref), "label": str(table.ref), "columns": len(table.columns)}
            for table in tables
        ]
        edges = [
            {
                "from": f"{edge.left.schema}.{edge.left.table}",
                "to": f"{edge.right.schema}.{edge.right.table}",
                "label": f"{edge.left.column} = {edge.right.column}",
                "evidence": edge.evidence,
            }
            for edge in analyst.mindmap.edges_for(user)
        ]
        return JSONResponse(content={"nodes": nodes, "edges": edges})
    except Exception:
        return _error("Unable to load the scoped data map.")


@app.get("/api/audit")
def audit() -> JSONResponse:
    try:
        chain_ok, chain_message = analyst.audit.verify_chain()
        entries = [_audit_payload(entry) for entry in analyst.audit.all()]
        return JSONResponse(content=jsonable_encoder({
            "chain_ok": chain_ok,
            "chain_message": chain_message,
            "entries": entries,
        }))
    except Exception:
        return _error("Unable to load audit records.")


@app.get("/", include_in_schema=False, response_model=None)
def index():
    try:
        return FileResponse(STATIC_DIR / "index.html")
    except Exception:
        return _error("Unable to load the application interface.")


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Entry point for `atlas-serve` — spin up the local web UI."""
    import uvicorn

    uvicorn.run("atlas.api.app:app", host=host, port=port)
