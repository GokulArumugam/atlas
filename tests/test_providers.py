"""WS3 provider tests: registry, encryption, profile store, ephemeral use."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_registry_lists_builtins():
    from atlas.agent.providers import get_registry
    names = get_registry().names()
    for expected in ("ollama", "openai", "anthropic", "deterministic"):
        assert expected in names, names


def test_encrypt_decrypt_roundtrip():
    from atlas.agent.providers.registry import _decrypt_key, _encrypt_key

    for pepper in ("short", "a much longer pepper that has entropy"):
        cipher = _encrypt_key("sk-secret-1234", pepper)
        assert "sk-secret-1234" not in cipher
        assert _decrypt_key(cipher, pepper) == "sk-secret-1234"


def test_encrypt_wrong_pepper_fails():
    from atlas.agent.providers.registry import _decrypt_key, _encrypt_key

    cipher = _encrypt_key("sk-abc", "pepper-1")
    import pytest
    with pytest.raises(Exception):
        _decrypt_key(cipher, "pepper-2")


def test_profile_store_save_and_load(tmp_path: Path):
    os.environ["ATLAS_PROVIDER_PROFILES_FILE"] = str(tmp_path / "profiles.json")
    os.environ["ATLAS_KEY_PEPPER"] = "unit-pepper"
    from atlas.agent.providers import registry as registry_mod
    registry_mod.reset_profile_store_for_tests()

    store = registry_mod.get_profile_store()
    store.save_profile(
        name="my-openai",
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-fake-1234",
        owner_user_id="gokul",
    )

    # Profile file on disk MUST NOT contain the plaintext api key.
    on_disk = (tmp_path / "profiles.json").read_text()
    assert "sk-fake-1234" not in on_disk

    config = store.load_config("my-openai", owner_user_id="gokul")
    assert config is not None
    assert config.api_key == "sk-fake-1234"
    assert config.provider == "openai"

    # Wrong owner returns None.
    assert store.load_config("my-openai", owner_user_id="mitra") is None


def test_ephemeral_resolution_builds_generator():
    from atlas.agent.providers import ProviderConfig, resolve_generator
    gen = resolve_generator(ephemeral=ProviderConfig(provider="deterministic"))
    assert type(gen).__name__ == "DeterministicGenerator"


def test_unknown_provider_raises():
    from atlas.agent.providers import ProviderConfig, resolve_generator
    import pytest
    with pytest.raises(ValueError):
        resolve_generator(ephemeral=ProviderConfig(provider="fictional-cloud-llm"))
