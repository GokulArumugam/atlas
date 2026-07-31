"""API-key authentication and identity resolution.

The API-key store maps opaque secret keys to identities. A caller presents a key
via the `X-Atlas-Key` header; we resolve it to an `Identity` (user_id, roles,
tenant, display name). The rest of the app should use `request.state.identity`
— never a body-supplied `user` field — as the source of truth for policy.

Keys are hashed at rest (SHA-256 over `pepper || key`). The pepper is loaded
from the secrets provider; if unset, a stable per-deploy default is used.
This is *not* password hashing — keys are high-entropy machine credentials, so
a fast, peppered hash is the right trade-off.

Store format (JSON, on disk):

    {
      "keys": [
        {
          "hash": "<hex sha256>",
          "user_id": "gokul",
          "roles": ["engineering"],
          "tenant_id": "default",
          "display_name": "Gokul (eng)",
          "disabled": false
        }
      ]
    }

To generate a key without editing files by hand, use `atlas.api.auth.mint_key`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from atlas.config.secrets import get_secrets
from atlas.config.settings import get_settings


HEADER_NAME = "X-Atlas-Key"
LEGACY_BODY_USER_ALLOWED_PATHS: set[str] = set()  # Filled below.

# Public routes that don't require authentication.
_PUBLIC_PATHS = {"/", "/api/health", "/api/livez", "/api/readyz"}
_PUBLIC_PREFIXES = ("/static/",)


@dataclass(frozen=True)
class Identity:
    """Resolved caller identity used everywhere downstream of the middleware."""

    user_id: str
    roles: tuple[str, ...] = ()
    tenant_id: str = "default"
    display_name: str = ""
    source: str = "api_key"  # "api_key" | "disabled_mode"

    @property
    def is_authenticated(self) -> bool:
        return self.source != "unauthenticated"


UNAUTHENTICATED = Identity(user_id="", source="unauthenticated")


class KeyStore:
    """Thread-safe on-disk key store.

    The store loads lazily and reloads if the file mtime changes, so operators
    can rotate keys without a restart. A missing file is treated as an empty
    store — that's the fail-closed default for a fresh install.
    """

    def __init__(self, path: Path, pepper: str) -> None:
        self._path = path
        self._pepper = pepper.encode("utf-8")
        self._mtime: float | None = None
        self._by_hash: dict[str, Identity] = {}
        self._lock = threading.Lock()

    def _hash(self, key: str) -> str:
        return hashlib.sha256(self._pepper + key.encode("utf-8")).hexdigest()

    def _load_if_changed(self) -> None:
        with self._lock:
            if not self._path.exists():
                if self._mtime is not None:
                    self._mtime = None
                    self._by_hash = {}
                return
            mtime = self._path.stat().st_mtime
            if self._mtime == mtime:
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._mtime = mtime
                self._by_hash = {}
                return
            entries = raw.get("keys", []) if isinstance(raw, dict) else []
            store: dict[str, Identity] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("disabled"):
                    continue
                key_hash = entry.get("hash")
                user_id = entry.get("user_id")
                if not isinstance(key_hash, str) or not isinstance(user_id, str):
                    continue
                roles_raw = entry.get("roles") or []
                if not isinstance(roles_raw, list):
                    roles_raw = []
                store[key_hash] = Identity(
                    user_id=user_id,
                    roles=tuple(str(role) for role in roles_raw),
                    tenant_id=str(entry.get("tenant_id") or "default"),
                    display_name=str(entry.get("display_name") or user_id),
                    source="api_key",
                )
            self._by_hash = store
            self._mtime = mtime

    def resolve(self, presented_key: str) -> Identity | None:
        if not presented_key:
            return None
        self._load_if_changed()
        key_hash = self._hash(presented_key)
        # Constant-time lookup: walk all entries with hmac.compare_digest.
        # For typical demo sizes this is negligible; for large stores this
        # matters less because SHA-256 is already deterministic on the hash.
        for stored_hash, identity in self._by_hash.items():
            if hmac.compare_digest(stored_hash, key_hash):
                return identity
        return None

    def all_identities(self) -> list[Identity]:
        self._load_if_changed()
        return list(self._by_hash.values())


_store: KeyStore | None = None
_store_lock = threading.Lock()


def get_key_store() -> KeyStore:
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            settings = get_settings()
            pepper = get_secrets().get("ATLAS_KEY_PEPPER") or "atlas-default-pepper-change-me"
            _store = KeyStore(settings.api_keys_path, pepper)
        return _store


def reset_key_store_for_tests() -> None:
    global _store
    _store = None


def mint_key(length: int = 32) -> str:
    """Return a fresh URL-safe key. Persisting it is the caller's job."""

    return "atlas_" + _secrets.token_urlsafe(length)


def hash_key(key: str, pepper: str | None = None) -> str:
    pepper_bytes = (pepper or get_secrets().get("ATLAS_KEY_PEPPER") or "atlas-default-pepper-change-me").encode("utf-8")
    return hashlib.sha256(pepper_bytes + key.encode("utf-8")).hexdigest()


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Populate `request.state.identity` from the `X-Atlas-Key` header.

    Fail-closed: when auth_mode is `enforced`, all non-public requests must
    present a valid key. Missing/invalid keys → 401.

    In `disabled` mode (dev/tests), the middleware synthesizes an identity from
    a body-supplied `user` for `POST /api/ask` and from the path for
    `/api/graph/{user}`. This preserves the current demo UX for the SPA while
    the real product runs in enforced mode.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path

        if _is_public(path):
            request.state.identity = UNAUTHENTICATED
            return await call_next(request)

        presented = request.headers.get(HEADER_NAME, "")
        identity: Identity | None = None
        if presented:
            identity = get_key_store().resolve(presented)

        if identity is None and settings.auth_mode == "disabled":
            # Demo/dev fallback: derive identity from request shape.
            identity = await _identity_from_body_or_path(request)

        if identity is None:
            return JSONResponse(
                status_code=401,
                content={"error": "Authentication required. Present an X-Atlas-Key header."},
            )

        request.state.identity = identity
        return await call_next(request)


async def _identity_from_body_or_path(request: Request) -> Identity | None:
    """Legacy identity resolution for `auth_mode=disabled`."""

    path = request.url.path
    if path.startswith("/api/graph/"):
        user_id = path.rsplit("/", 1)[-1]
        if user_id:
            return Identity(user_id=user_id, source="disabled_mode", display_name=user_id)

    if request.method == "POST" and path in {"/api/ask", "/api/run-sql"}:
        try:
            payload = await request.json()
        except Exception:
            return None
        if isinstance(payload, dict):
            user_id = str(payload.get("user") or "").strip()
            if user_id:
                # Cache the parsed body on the request so the handler doesn't
                # need to re-read (Starlette drains the stream on json()).
                request.state.parsed_body = payload
                return Identity(user_id=user_id, source="disabled_mode", display_name=user_id)

    # For everything else in disabled mode, allow anonymous read-only access.
    if request.method == "GET":
        return Identity(user_id="", source="disabled_mode", display_name="anonymous")
    return None


def identity_of(request: Request) -> Identity:
    return getattr(request.state, "identity", UNAUTHENTICATED)


def require_role(identity: Identity, roles: Iterable[str]) -> bool:
    wanted = set(roles)
    if not wanted:
        return True
    return bool(wanted.intersection(identity.roles))
