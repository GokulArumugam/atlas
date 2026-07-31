"""Cross-cutting HTTP middleware: security headers, request size cap, rate limit.

Kept intentionally dependency-free (no slowapi) — the rate limiter is a fixed
window token bucket keyed by identity user_id (or client host if unauthenticated).
That's good enough for a single-instance demo and honest about its limits;
production deploys should terminate at a real gateway.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from atlas.api.auth import identity_of
from atlas.config.settings import get_settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds a defensible baseline of security response headers.

    The CSP is intentionally strict — same-origin scripts only, no inline JS.
    The SPA already uses external `static/app.js`, so this doesn't break it.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        settings = get_settings()
        response.headers.setdefault("Content-Security-Policy", settings.content_security_policy)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # Only send HSTS on HTTPS — sending it on plain HTTP is technically valid
        # but confusing for local dev.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured cap.

    We intentionally don't chunk-read to enforce the limit against streaming
    bodies — Starlette already reads the full body for handlers that need it,
    and this is a defense-in-depth layer, not the primary control (uvicorn's
    `--limit-request-line` and reverse-proxy caps are the primary).
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        length_header = request.headers.get("content-length")
        if length_header:
            try:
                length = int(length_header)
            except ValueError:
                return JSONResponse(status_code=400, content={"error": "Malformed Content-Length."})
            if length > settings.max_request_bytes:
                return JSONResponse(
                    status_code=413, content={"error": "Request payload too large."}
                )
        return await call_next(request)


class _TokenBucket:
    __slots__ = ("tokens", "last_refill", "initialized")

    def __init__(self, capacity: float) -> None:
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self.initialized = False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory token bucket keyed by identity or client host.

    Only rate-limits POST /api/ask by default because it drives the expensive
    LLM call. Read-only endpoints are cheap and left alone.
    """

    def __init__(self, app, protected_paths: set[str] | None = None) -> None:
        super().__init__(app)
        self._protected = protected_paths or {"/api/ask"}
        self._buckets: dict[str, _TokenBucket] = defaultdict(lambda: _TokenBucket(0))
        self._lock = threading.Lock()

    def _key(self, request: Request) -> str:
        identity = identity_of(request)
        if identity.user_id:
            return f"user:{identity.user_id}"
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    def _consume(self, key: str, per_minute: int) -> tuple[bool, float]:
        now = time.monotonic()
        refill_rate = per_minute / 60.0
        with self._lock:
            bucket = self._buckets[key]
            if not bucket.initialized:
                bucket.tokens = float(per_minute)
                bucket.last_refill = now
                bucket.initialized = True
            elapsed = now - bucket.last_refill
            bucket.tokens = min(float(per_minute), bucket.tokens + elapsed * refill_rate)
            bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, bucket.tokens
            retry_after = (1.0 - bucket.tokens) / refill_rate if refill_rate > 0 else 60.0
            return False, retry_after

    async def dispatch(self, request: Request, call_next):
        if request.url.path not in self._protected:
            return await call_next(request)
        settings = get_settings()
        per_minute = max(1, settings.rate_limit_per_minute)
        allowed, meta = self._consume(self._key(request), per_minute)
        if not allowed:
            retry = max(1, int(meta))
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded."},
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)


def install_cors(app, on_startup: Callable | None = None) -> None:
    """Attach CORS if any origins are configured, else skip cleanly."""

    from fastapi.middleware.cors import CORSMiddleware

    settings = get_settings()
    if not settings.allowed_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["X-Atlas-Key", "Content-Type", "X-Request-ID"],
        allow_credentials=False,
        max_age=600,
    )
