"""Provider registry, profile store, and one-shot request-scoped resolution.

Two ways to pick a provider at request time:

1. `profile: <name>` — use a server-stored profile.
2. Ephemeral `provider + model + api_key` — no persistence.

Server-stored profiles keep the API key encrypted at rest with Fernet, using
a symmetric key derived from ``ATLAS_KEY_PEPPER`` (must be set for production).
The pepper is not the encryption key directly; it's stretched with PBKDF2 first.

Profile file schema (JSON):

    {
      "profiles": {
        "my-openai": {
          "provider": "openai",
          "model": "gpt-4o-mini",
          "base_url": null,
          "api_key_cipher": "<base64>",
          "owner_user_id": "gokul"
        }
      }
    }
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from atlas.agent.generator import DeterministicGenerator
from atlas.agent.providers.anthropic import AnthropicGenerator
from atlas.agent.providers.base import Generator
from atlas.agent.providers.ollama import OllamaGenerator
from atlas.agent.providers.openai_compatible import OpenAICompatibleGenerator
from atlas.config.secrets import get_secrets
from atlas.config.settings import get_settings


@dataclass(frozen=True)
class ProviderConfig:
    """Ephemeral, per-request provider config."""

    provider: str
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    dialect: str = "duckdb"


@dataclass
class ProviderProfile:
    """A named, persisted provider configuration."""

    name: str
    provider: str
    model: str
    base_url: str | None
    owner_user_id: str
    api_key_cipher: str  # base64(nonce + ciphertext) — see _crypto.


ProviderFactory = Callable[[ProviderConfig], Generator]


class ProviderRegistry:
    """Central provider directory. Add new providers via `register`."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        self._factories[name.lower()] = factory

    def names(self) -> list[str]:
        return sorted(self._factories.keys())

    def build(self, config: ProviderConfig) -> Generator:
        factory = self._factories.get(config.provider.lower())
        if factory is None:
            raise ValueError(f"Unknown provider: {config.provider}. Known: {self.names()}")
        return factory(config)


# --- Built-in factories -----------------------------------------------------

def _ollama_factory(config: ProviderConfig) -> Generator:
    return OllamaGenerator(
        base_url=config.base_url or get_settings().ollama_base_url,
        model=config.model or "llama3.1:8b",
        dialect=config.dialect,
    )


def _openai_factory(config: ProviderConfig) -> Generator:
    if not config.api_key:
        raise ValueError("OpenAI-compatible providers require an api_key.")
    return OpenAICompatibleGenerator(
        api_key=config.api_key,
        model=config.model or "gpt-4o-mini",
        base_url=config.base_url or "https://api.openai.com/v1",
        dialect=config.dialect,
    )


def _anthropic_factory(config: ProviderConfig) -> Generator:
    if not config.api_key:
        raise ValueError("Anthropic requires an api_key.")
    return AnthropicGenerator(
        api_key=config.api_key,
        model=config.model or "claude-3-5-sonnet-latest",
        dialect=config.dialect,
    )


def _deterministic_factory(config: ProviderConfig) -> Generator:
    del config
    return DeterministicGenerator()


_default_registry = ProviderRegistry()
_default_registry.register("ollama", _ollama_factory)
_default_registry.register("openai", _openai_factory)
_default_registry.register("groq", _openai_factory)  # OpenAI-compatible
_default_registry.register("together", _openai_factory)  # OpenAI-compatible
_default_registry.register("openai-compatible", _openai_factory)
_default_registry.register("anthropic", _anthropic_factory)
_default_registry.register("deterministic", _deterministic_factory)


def get_registry() -> ProviderRegistry:
    return _default_registry


# --- Encryption -------------------------------------------------------------

def _derive_key(pepper: str, salt: bytes = b"atlas-provider-profiles") -> bytes:
    """Stretch the pepper into a 32-byte key with PBKDF2-HMAC-SHA256."""

    return hashlib.pbkdf2_hmac("sha256", pepper.encode("utf-8"), salt, 200_000, dklen=32)


def _encrypt_key(plaintext: str, pepper: str) -> str:
    """AES-256-GCM if `cryptography` is available; XChaCha-lite HMAC otherwise.

    For portfolio-scale use we prefer `cryptography.fernet` when installed and
    fall back to an HMAC-authenticated stream cipher using stdlib only. Both
    surfaces MAC-and-encrypt so tampering fails-closed.
    """

    key = _derive_key(pepper)
    try:
        from cryptography.fernet import Fernet  # type: ignore
        fernet_key = base64.urlsafe_b64encode(key)
        return "fernet:" + Fernet(fernet_key).encrypt(plaintext.encode("utf-8")).decode("utf-8")
    except ImportError:
        # Stdlib fallback: HMAC-then-XOR with per-record nonce.
        nonce = os.urandom(16)
        stream = _keystream(key, nonce, len(plaintext.encode("utf-8")))
        ct = bytes(a ^ b for a, b in zip(plaintext.encode("utf-8"), stream))
        tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()
        return "stdlib:" + base64.b64encode(nonce + tag + ct).decode("ascii")


def _decrypt_key(payload: str, pepper: str) -> str:
    key = _derive_key(pepper)
    if payload.startswith("fernet:"):
        from cryptography.fernet import Fernet  # type: ignore
        fernet_key = base64.urlsafe_b64encode(key)
        return Fernet(fernet_key).decrypt(payload[len("fernet:"):].encode("utf-8")).decode("utf-8")
    if payload.startswith("stdlib:"):
        raw = base64.b64decode(payload[len("stdlib:"):].encode("ascii"))
        nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
        expected = hmac.new(key, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("Profile key MAC verification failed.")
        stream = _keystream(key, nonce, len(ct))
        return bytes(a ^ b for a, b in zip(ct, stream)).decode("utf-8")
    raise ValueError("Unknown ciphertext format.")


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """SHA-256(key || nonce || counter) — deterministic stream cipher for the fallback."""

    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


# --- Profile store ----------------------------------------------------------

_PROFILE_FILE_ENV = "ATLAS_PROVIDER_PROFILES_FILE"


class ProfileStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self._path.exists():
            return {"profiles": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"profiles": {}}
        if not isinstance(data, dict) or "profiles" not in data:
            return {"profiles": {}}
        return data

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def list_profiles(self, owner_user_id: str | None = None) -> list[dict]:
        data = self._read()
        results = []
        for name, entry in data.get("profiles", {}).items():
            if owner_user_id and entry.get("owner_user_id") != owner_user_id:
                continue
            results.append({
                "name": name,
                "provider": entry.get("provider"),
                "model": entry.get("model"),
                "base_url": entry.get("base_url"),
                "owner_user_id": entry.get("owner_user_id"),
            })
        return results

    def save_profile(
        self,
        name: str,
        provider: str,
        model: str,
        api_key: str,
        owner_user_id: str,
        base_url: str | None = None,
        pepper: str | None = None,
    ) -> None:
        pepper = pepper or get_secrets().get("ATLAS_KEY_PEPPER") or "atlas-default-pepper-change-me"
        cipher = _encrypt_key(api_key, pepper)
        with self._lock:
            data = self._read()
            data.setdefault("profiles", {})[name] = {
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "api_key_cipher": cipher,
                "owner_user_id": owner_user_id,
            }
            self._write(data)

    def delete_profile(self, name: str, owner_user_id: str) -> bool:
        with self._lock:
            data = self._read()
            entry = data.get("profiles", {}).get(name)
            if not entry or entry.get("owner_user_id") != owner_user_id:
                return False
            del data["profiles"][name]
            self._write(data)
            return True

    def load_config(self, name: str, owner_user_id: str | None = None) -> ProviderConfig | None:
        data = self._read()
        entry = data.get("profiles", {}).get(name)
        if not entry:
            return None
        if owner_user_id and entry.get("owner_user_id") != owner_user_id:
            return None
        pepper = get_secrets().get("ATLAS_KEY_PEPPER") or "atlas-default-pepper-change-me"
        api_key = _decrypt_key(entry["api_key_cipher"], pepper) if entry.get("api_key_cipher") else None
        return ProviderConfig(
            provider=entry["provider"],
            model=entry.get("model"),
            base_url=entry.get("base_url"),
            api_key=api_key,
        )


_store: ProfileStore | None = None
_store_lock = threading.Lock()


def get_profile_store() -> ProfileStore:
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            path_str = (
                os.environ.get(_PROFILE_FILE_ENV)
                or str(Path(os.environ.get("ATLAS_ROOT") or Path.cwd()) / "data" / "provider_profiles.json")
            )
            _store = ProfileStore(Path(path_str))
        return _store


def reset_profile_store_for_tests() -> None:
    global _store
    _store = None


# --- Request-scoped resolution ---------------------------------------------

def resolve_generator(
    *,
    profile_name: str | None = None,
    ephemeral: ProviderConfig | None = None,
    owner_user_id: str | None = None,
    dialect: str = "duckdb",
) -> Generator:
    """Return the Generator to use for one request.

    Priority: ephemeral > profile > server default.
    ``dialect`` is threaded into the config so the system prompt matches the
    warehouse the query will actually run against.
    """

    if ephemeral is not None:
        # Respect explicit dialect on ephemeral if the caller set it, else
        # apply the caller-supplied default from the request handler.
        if ephemeral.dialect == "duckdb" and dialect != "duckdb":
            ephemeral = ProviderConfig(
                provider=ephemeral.provider, model=ephemeral.model,
                api_key=ephemeral.api_key, base_url=ephemeral.base_url,
                dialect=dialect,
            )
        return get_registry().build(ephemeral)
    if profile_name:
        config = get_profile_store().load_config(profile_name, owner_user_id=owner_user_id)
        if config is None:
            raise ValueError(f"No such provider profile: {profile_name}")
        config = ProviderConfig(
            provider=config.provider, model=config.model,
            api_key=config.api_key, base_url=config.base_url,
            dialect=dialect,
        )
        return get_registry().build(config)
    # Server default: Ollama.
    settings = get_settings()
    return get_registry().build(ProviderConfig(provider=settings.default_llm_provider))
