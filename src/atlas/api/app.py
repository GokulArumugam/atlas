"""FastAPI wrapper around the analyst — governance-first, defense-in-depth."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from atlas.agent.analyst import Analyst, AnalystAnswer
from atlas.agent.providers import ProviderConfig, get_profile_store, get_registry, resolve_generator
from atlas.api.auth import AuthMiddleware, Identity, identity_of
from atlas.api.middleware import (
    RateLimitMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    install_cors,
)
from atlas.audit.audit import AuditEntry
from atlas.config.settings import get_settings
from atlas.connector import DuckDBConnector
from atlas.observability.logging import RequestIdMiddleware, configure_logging, get_logger


import os

ROOT = Path(os.environ.get("ATLAS_ROOT") or Path.cwd()).resolve()
STATIC_DIR = Path(os.environ.get("ATLAS_STATIC_DIR") or (ROOT / "static"))
DB_PATH = Path(os.environ.get("ATLAS_DB_PATH") or (ROOT / "data" / "warehouse.duckdb"))
AUDIT_PATH = Path(os.environ.get("ATLAS_AUDIT_PATH") or (ROOT / "data" / "audit.duckdb"))


def _build_analyst() -> Analyst:
    return Analyst(connector=DuckDBConnector(str(DB_PATH)), audit_path=str(AUDIT_PATH))


class _State:
    """Container for lazily-initialised singletons attached to the app."""

    analyst: Analyst | None = None


state = _State()
logger = get_logger("atlas.api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_logging()
    logger.info("atlas_starting")
    # Analyst init is deferred but eagerly attempted so failures surface here,
    # not on the first request. Any real refactor should split "index-heavy"
    # from "index-light" initialisation, but for the demo, eager is fine.
    state.analyst = _build_analyst()
    logger.info("atlas_ready")
    try:
        yield
    finally:
        logger.info("atlas_shutting_down")


app = FastAPI(
    title="Atlas — Governed AI Data Analyst",
    version="1.0",
    description=(
        "Text-to-SQL with a fail-closed firewall, per-user policy scope, and a "
        "tamper-evident audit trail. Authentication via X-Atlas-Key header."
    ),
    lifespan=_lifespan,
)


# Middleware order matters: request-id first so all others log with a rid;
# security headers wrap the outer response; size limit and CORS before auth so
# preflight and payload rejection don't require a key; auth resolves identity;
# rate-limit runs after auth to key off the resolved identity.
install_cors(app)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class AskRequest(BaseModel):
    question: str = Field(..., max_length=8000)
    # `user` is accepted for backward-compat when auth_mode=disabled, but it is
    # ignored whenever an authenticated identity is present.
    user: str | None = None
    # Optional provider selection. Priority: ephemeral > profile > server default.
    provider_profile: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, max_length=256)
    base_url: str | None = Field(default=None, max_length=512)


class SaveProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    provider: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=128)
    api_key: str = Field(..., min_length=1, max_length=256)
    base_url: str | None = Field(default=None, max_length=512)


def _error(message: str, status_code: int = 500) -> JSONResponse:
    """Return a deliberately plain error response, never an exception detail."""
    return JSONResponse(status_code=status_code, content={"error": message})


def _analyst() -> Analyst:
    if state.analyst is None:  # pragma: no cover — lifespan guarantees this
        state.analyst = _build_analyst()
    return state.analyst


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
    return asdict(entry)


@app.exception_handler(Exception)
async def _unexpected_error(_request: Request, error: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", exc_info=error)
    return _error("The service encountered an unexpected error. Please try again.")


@app.get("/api/health", include_in_schema=False)
def health() -> JSONResponse:
    return JSONResponse(content={"ok": True})


@app.get("/api/livez", include_in_schema=False)
def livez() -> JSONResponse:
    return JSONResponse(content={"ok": True})


@app.get("/api/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    ready = state.analyst is not None
    return JSONResponse(status_code=200 if ready else 503, content={"ready": ready})


@app.get("/api/metrics", include_in_schema=False)
def metrics(request: Request) -> JSONResponse:
    identity = identity_of(request)
    if identity.source == "api_key" and "admin" not in identity.roles and "audit" not in identity.roles:
        return _error("Metrics access requires the 'admin' or 'audit' role.", 403)
    analyst = state.analyst
    if analyst is None:
        return JSONResponse(content={"ready": False})
    return JSONResponse(content={
        "ready": True,
        "sql_cache": analyst.cache_stats(),
        "catalog_tables": len(analyst.catalog.all_tables()),
    })


@app.get("/api/dashboard")
def dashboard(request: Request) -> JSONResponse:
    """Governance dashboard summary: decisions, mask rate, PII exposure attempts.

    Reads only from the audit log — no warehouse access. Requires the
    ``admin`` or ``audit`` role in enforced mode; open to anonymous callers in
    disabled mode."""
    identity = identity_of(request)
    if identity.source == "api_key" and "admin" not in identity.roles and "audit" not in identity.roles:
        return _error("Dashboard access requires the 'admin' or 'audit' role.", 403)
    analyst = state.analyst
    if analyst is None:
        return _error("Analyst not ready.", 503)
    try:
        entries = analyst.audit.all(limit=10_000)
        totals = {"allow": 0, "mask": 0, "deny": 0}
        pii_attempts = 0
        top_users: dict[str, int] = {}
        top_denials: list[dict] = []
        for entry in entries:
            totals[entry.decision] = totals.get(entry.decision, 0) + 1
            top_users[entry.user] = top_users.get(entry.user, 0) + 1
            if entry.masked_columns:
                pii_attempts += 1
            if entry.decision == "deny" and len(top_denials) < 20:
                top_denials.append({
                    "audit_id": entry.audit_id,
                    "user": entry.user,
                    "question": entry.question,
                    "reason": entry.reason,
                    "ts": entry.ts,
                })
        total = sum(totals.values()) or 1
        return JSONResponse(content={
            "total_decisions": total,
            "decision_counts": totals,
            "decision_rates": {k: round(v / total, 4) for k, v in totals.items()},
            "pii_mask_events": pii_attempts,
            "top_users": sorted(
                [{"user": u, "count": c} for u, c in top_users.items()],
                key=lambda x: x["count"], reverse=True,
            )[:10],
            "recent_denials": top_denials,
        })
    except Exception:
        logger.exception("dashboard_failed")
        return _error("Unable to load governance dashboard.")


@app.get("/api/users")
def users(request: Request) -> JSONResponse:
    try:
        analyst = _analyst()
        return JSONResponse(content=[
            {"user": user, "team": analyst.policy.team_of(user)}
            for user in analyst.policy.users()
        ])
    except Exception:
        logger.exception("users_failed")
        return _error("Unable to load users right now.")


import asyncio


@app.post("/api/ask")
async def ask(payload: AskRequest, request: Request) -> JSONResponse:
    identity = identity_of(request)
    try:
        settings = get_settings()
        question = payload.question.strip()
        if not question:
            return _error("A question is required.", 400)
        if len(question) > settings.max_question_chars:
            return _error(
                f"Question is too long (max {settings.max_question_chars} chars).",
                400,
            )
        user_id = _resolve_user_id(identity, payload)
        if not user_id:
            return _error("Authenticated identity has no user_id.", 401)

        # Optional per-request provider selection.
        override_generator = None
        try:
            if payload.provider:
                ephemeral = ProviderConfig(
                    provider=payload.provider,
                    model=payload.model,
                    api_key=payload.api_key,
                    base_url=payload.base_url,
                )
                override_generator = resolve_generator(
                    ephemeral=ephemeral, owner_user_id=user_id
                )
            elif payload.provider_profile:
                override_generator = resolve_generator(
                    profile_name=payload.provider_profile, owner_user_id=user_id
                )
        except ValueError as exc:
            return _error(str(exc), 400)

        answer = await asyncio.to_thread(
            _analyst().ask, user_id, question, generator=override_generator
        )
        return JSONResponse(content=jsonable_encoder(_answer_payload(answer)))
    except Exception:
        logger.exception("ask_failed")
        return _error("Unable to complete that governed query.")


@app.get("/api/providers")
def list_providers(request: Request) -> JSONResponse:
    """List all provider names the server knows how to build."""
    return JSONResponse(content={"providers": get_registry().names()})


@app.get("/api/providers/profiles")
def list_profiles(request: Request) -> JSONResponse:
    identity = identity_of(request)
    if not identity.user_id and identity.source != "disabled_mode":
        return _error("Authenticated identity required.", 401)
    owner = identity.user_id or "anonymous"
    return JSONResponse(content={
        "profiles": get_profile_store().list_profiles(owner_user_id=owner),
    })


@app.post("/api/providers/profiles")
def save_profile(payload: SaveProfileRequest, request: Request) -> JSONResponse:
    identity = identity_of(request)
    if not identity.user_id and identity.source != "disabled_mode":
        return _error("Authenticated identity required.", 401)
    owner = identity.user_id or "anonymous"
    try:
        # Validate provider name up-front so save_profile doesn't accept junk.
        if payload.provider.lower() not in get_registry().names():
            return _error(
                f"Unknown provider '{payload.provider}'. Known: {get_registry().names()}",
                400,
            )
        get_profile_store().save_profile(
            name=payload.name,
            provider=payload.provider,
            model=payload.model,
            api_key=payload.api_key,
            base_url=payload.base_url,
            owner_user_id=owner,
        )
        return JSONResponse(content={"ok": True, "name": payload.name})
    except Exception:
        logger.exception("save_profile_failed")
        return _error("Unable to save provider profile.")


@app.delete("/api/providers/profiles/{name}")
def delete_profile(name: str, request: Request) -> JSONResponse:
    identity = identity_of(request)
    if not identity.user_id and identity.source != "disabled_mode":
        return _error("Authenticated identity required.", 401)
    owner = identity.user_id or "anonymous"
    ok = get_profile_store().delete_profile(name, owner_user_id=owner)
    if not ok:
        return _error("Profile not found.", 404)
    return JSONResponse(content={"ok": True})


def _resolve_user_id(identity: Identity, payload: AskRequest) -> str:
    if identity.user_id:
        return identity.user_id
    # disabled-mode + missing header: honour body user for legacy callers.
    if get_settings().auth_mode == "disabled" and payload.user:
        return payload.user.strip()
    return ""


@app.get("/api/graph/{user}")
def graph(user: str, request: Request) -> JSONResponse:
    identity = identity_of(request)
    try:
        # Authenticated callers can only view their own graph unless they hold
        # the `admin` role. Disabled-mode falls back to the path segment.
        if identity.source == "api_key":
            if "admin" not in identity.roles and user != identity.user_id:
                return _error("You may only view your own scoped data map.", 403)
        analyst = _analyst()
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
        logger.exception("graph_failed")
        return _error("Unable to load the scoped data map.")


@app.get("/api/audit")
def audit(request: Request) -> JSONResponse:
    identity = identity_of(request)
    if identity.source == "api_key" and "audit" not in identity.roles and "admin" not in identity.roles:
        return _error("Audit access requires the 'audit' or 'admin' role.", 403)
    try:
        analyst = _analyst()
        chain_ok, chain_message = analyst.audit.verify_chain()
        entries = [_audit_payload(entry) for entry in analyst.audit.all()]
        return JSONResponse(content=jsonable_encoder({
            "chain_ok": chain_ok,
            "chain_message": chain_message,
            "entries": entries,
        }))
    except Exception:
        logger.exception("audit_failed")
        return _error("Unable to load audit records.")


@app.get("/", include_in_schema=False, response_model=None)
def index():
    try:
        return FileResponse(STATIC_DIR / "index.html")
    except Exception:
        logger.exception("index_failed")
        return _error("Unable to load the application interface.")


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Entry point for `atlas-serve` — spin up the local web UI."""
    import uvicorn

    configure_logging()
    uvicorn.run("atlas.api.app:app", host=host, port=port)
