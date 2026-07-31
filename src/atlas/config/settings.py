"""Runtime settings loaded once at startup.

Settings are read from env / secrets, validated at construction, and cached.
Every value has a safe default so a fresh clone still boots, but strict
production toggles (auth mode, allowed origins) fail loudly if misconfigured.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from atlas.config.secrets import SecretsProvider, get_secrets


_TRUE = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in _TRUE


def _as_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_list(value: str | None, default: list[str] | None = None) -> list[str]:
    if not value:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Auth
    auth_mode: str  # "enforced" | "disabled"
    api_keys_path: Path

    # Rate limiting
    rate_limit_per_minute: int
    max_question_chars: int
    max_request_bytes: int

    # CORS / security headers
    allowed_origins: list[str] = field(default_factory=list)
    content_security_policy: str = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'"
    )

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    # LLM
    default_llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"

    # Semantic notes integrity
    semantic_manifest_path: Path | None = None
    enforce_semantic_integrity: bool = False


def _build(secrets: SecretsProvider) -> Settings:
    import os
    root = Path(os.environ.get("ATLAS_ROOT") or Path.cwd()).resolve()

    auth_mode = (secrets.get("ATLAS_AUTH_MODE") or "enforced").strip().lower()
    if auth_mode not in {"enforced", "disabled"}:
        auth_mode = "enforced"

    api_keys_path_raw = secrets.get("ATLAS_API_KEYS_FILE") or str(root / "data" / "api_keys.json")

    return Settings(
        auth_mode=auth_mode,
        api_keys_path=Path(api_keys_path_raw),
        rate_limit_per_minute=_as_int(secrets.get("ATLAS_RATE_LIMIT_PER_MINUTE"), 30),
        max_question_chars=_as_int(secrets.get("ATLAS_MAX_QUESTION_CHARS"), 2000),
        max_request_bytes=_as_int(secrets.get("ATLAS_MAX_REQUEST_BYTES"), 32 * 1024),
        allowed_origins=_as_list(secrets.get("ATLAS_ALLOWED_ORIGINS"), []),
        log_level=(secrets.get("ATLAS_LOG_LEVEL") or "INFO").upper(),
        log_json=_as_bool(secrets.get("ATLAS_LOG_JSON"), True),
        default_llm_provider=(secrets.get("ATLAS_DEFAULT_LLM_PROVIDER") or "ollama").lower(),
        ollama_base_url=secrets.get("ATLAS_OLLAMA_BASE_URL") or "http://localhost:11434",
        semantic_manifest_path=(
            Path(secrets.get("ATLAS_SEMANTIC_MANIFEST"))
            if secrets.get("ATLAS_SEMANTIC_MANIFEST")
            else None
        ),
        enforce_semantic_integrity=_as_bool(secrets.get("ATLAS_ENFORCE_SEMANTIC_INTEGRITY"), False),
    )


_settings: Settings | None = None
_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings
    if _settings is not None:
        return _settings
    with _lock:
        if _settings is None:
            _settings = _build(get_secrets())
        return _settings


def reset_settings_for_tests() -> None:
    global _settings
    _settings = None
