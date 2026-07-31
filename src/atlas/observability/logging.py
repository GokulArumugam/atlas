"""Stdlib-only structured JSON logging with per-request correlation IDs.

Uses a `contextvars.ContextVar` so log records emitted from any await point
during a request carry that request's ID. Kept dependency-free — we don't need
structlog for this scale.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from atlas.config.settings import get_settings


REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("atlas_request_id", default="-")
USER_ID: contextvars.ContextVar[str] = contextvars.ContextVar("atlas_user_id", default="-")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": REQUEST_ID.get(),
            "user_id": USER_ID.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "atlas_extra", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{record.levelname:<7} {record.name} [rid={REQUEST_ID.get()} uid={USER_ID.get()}] {record.getMessage()}"
        extra = getattr(record, "atlas_extra", None)
        if extra:
            base += " " + " ".join(f"{k}={v}" for k, v in extra.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter() if settings.log_json else _TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    # Quiet noisy uvicorn access log — we log requests ourselves.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def log_with(logger: logging.Logger, level: int, msg: str, **extra: Any) -> None:
    logger.log(level, msg, extra={"atlas_extra": extra})


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a request ID for the duration of the request and log start/end."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._logger = get_logger("atlas.access")

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        rid_token = REQUEST_ID.set(rid)
        uid_token = USER_ID.set("-")
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            # Update user_id from resolved identity, if any.
            identity = getattr(request.state, "identity", None)
            if identity and getattr(identity, "user_id", ""):
                USER_ID.set(identity.user_id)
            response.headers.setdefault("X-Request-ID", rid)
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log_with(
                self._logger,
                logging.INFO,
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status,
                duration_ms=duration_ms,
            )
            REQUEST_ID.reset(rid_token)
            USER_ID.reset(uid_token)
