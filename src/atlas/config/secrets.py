"""Pluggable secrets loading.

Providers are selected via the `ATLAS_SECRETS_PROVIDER` env variable:

* `env` (default) — read secrets directly from the process environment.
* `file` — read secrets from a JSON file at `ATLAS_SECRETS_FILE`. This matches
  the Docker/Podman `secrets:` model where files under `/run/secrets/` are the
  canonical mount point for credentials.

The abstraction is deliberately small: `get(name, default=None)` returns a
string or None. Callers should treat "None" as "unset" and refuse to run in
strict modes rather than silently defaulting.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Protocol


class SecretsProvider(Protocol):
    def get(self, name: str, default: str | None = None) -> str | None:
        """Return the secret value for `name`, or `default` if unset."""


class EnvSecretsProvider:
    """Read secrets from the process environment.

    Suitable for local dev and orchestration systems that inject secrets as env
    vars. In production we recommend the file provider so keys aren't visible
    in `/proc/self/environ` to other processes in the same PID namespace.
    """

    def get(self, name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name)
        return value if value is not None else default


class FileSecretsProvider:
    """Read secrets from a JSON file (e.g. Docker/Podman secret mount).

    The file must be JSON `{"KEY": "value", ...}` and readable only by the
    running user. Missing files are treated as "no secrets" — the provider
    falls back to the env for unknown names, matching real-world deployment
    patterns where infra secrets sit in the file and app tunables sit in env.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._cache: dict[str, str] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        with self._lock:
            if self._cache is not None:
                return self._cache
            if not self._path.exists():
                self._cache = {}
                return self._cache
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._cache = {}
                return self._cache
            if not isinstance(data, dict):
                self._cache = {}
                return self._cache
            self._cache = {str(k): str(v) for k, v in data.items()}
            return self._cache

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self._load().get(name)
        if value is not None:
            return value
        return os.environ.get(name, default)


_singleton: SecretsProvider | None = None
_singleton_lock = threading.Lock()


def get_secrets() -> SecretsProvider:
    """Return the process-wide secrets provider, constructed lazily."""

    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is not None:
            return _singleton
        provider_name = os.environ.get("ATLAS_SECRETS_PROVIDER", "env").lower()
        if provider_name == "file":
            path = os.environ.get("ATLAS_SECRETS_FILE", "/run/secrets/atlas.json")
            _singleton = FileSecretsProvider(path)
        else:
            _singleton = EnvSecretsProvider()
        return _singleton


def reset_secrets_for_tests() -> None:
    """Clear the singleton — tests only."""

    global _singleton
    _singleton = None
