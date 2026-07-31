"""Security-focused tests: API-key auth, rate limit, size cap, headers."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path


def _fresh_app_with_env(**env):
    # Reset cached settings/secrets/keystore so env changes take effect.
    for key, value in env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    for mod in [
        "atlas.api.app",
        "atlas.api.auth",
        "atlas.api.middleware",
        "atlas.config.settings",
        "atlas.config.secrets",
        "atlas.observability.logging",
    ]:
        sys.modules.pop(mod, None)

    from atlas.config import secrets as secrets_mod  # noqa: WPS433
    secrets_mod.reset_secrets_for_tests()
    from atlas.config import settings as settings_mod  # noqa: WPS433
    settings_mod.reset_settings_for_tests()

    module = importlib.import_module("atlas.api.app")
    return module


def test_enforced_mode_rejects_missing_key(tmp_path: Path):
    keys_path = tmp_path / "api_keys.json"
    keys_path.write_text(json.dumps({"keys": []}))

    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="enforced",
        ATLAS_API_KEYS_FILE=str(keys_path),
    )
    from fastapi.testclient import TestClient
    client = TestClient(module.app)

    r = client.post("/api/ask", json={"question": "average salary by department"})
    assert r.status_code == 401
    assert "authentication" in r.json()["error"].lower()

    r = client.get("/api/audit")
    assert r.status_code == 401


def test_enforced_mode_accepts_valid_key(tmp_path: Path):
    keys_path = tmp_path / "api_keys.json"
    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="enforced",
        ATLAS_API_KEYS_FILE=str(keys_path),
        ATLAS_KEY_PEPPER="unit-test",
    )
    from atlas.api.auth import hash_key, mint_key
    key = mint_key()
    keys_path.write_text(json.dumps({
        "keys": [
            {
                "hash": hash_key(key, "unit-test"),
                "user_id": "priya",
                "roles": ["hr"],
                "tenant_id": "default",
                "display_name": "Priya",
                "disabled": False,
            }
        ]
    }))

    from fastapi.testclient import TestClient
    client = TestClient(module.app)

    r = client.post(
        "/api/ask",
        json={"question": "average salary by department"},
        headers={"X-Atlas-Key": key},
    )
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "allow"


def test_enforced_mode_ignores_body_user_for_identity(tmp_path: Path):
    """A body 'user' must NOT override the authenticated identity."""

    keys_path = tmp_path / "api_keys.json"
    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="enforced",
        ATLAS_API_KEYS_FILE=str(keys_path),
        ATLAS_KEY_PEPPER="unit-test",
    )
    from atlas.api.auth import hash_key, mint_key
    key = mint_key()
    # This key belongs to engineering (gokul), which cannot see HR salary.
    keys_path.write_text(json.dumps({
        "keys": [
            {
                "hash": hash_key(key, "unit-test"),
                "user_id": "gokul",
                "roles": ["engineering"],
                "tenant_id": "default",
                "display_name": "Gokul",
                "disabled": False,
            }
        ]
    }))

    from fastapi.testclient import TestClient
    client = TestClient(module.app)

    r = client.post(
        "/api/ask",
        json={"user": "priya", "question": "average salary by department"},
        headers={"X-Atlas-Key": key},
    )
    assert r.status_code == 200
    # Because identity is gokul (engineering), HR salary access must be denied.
    assert r.json()["decision"] == "deny"


def test_security_headers_present(tmp_path: Path):
    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="enforced",
        ATLAS_API_KEYS_FILE=str(tmp_path / "keys.json"),
    )
    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in r.headers
    assert "X-Request-ID" in r.headers


def test_request_size_limit(tmp_path: Path):
    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="disabled",
        ATLAS_MAX_REQUEST_BYTES="128",
        ATLAS_API_KEYS_FILE=str(tmp_path / "keys.json"),
    )
    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    huge = "x" * 500
    r = client.post("/api/ask", json={"user": "gokul", "question": huge})
    assert r.status_code == 413


def test_question_length_cap(tmp_path: Path):
    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="disabled",
        ATLAS_MAX_QUESTION_CHARS="20",
        ATLAS_MAX_REQUEST_BYTES="32768",
        ATLAS_API_KEYS_FILE=str(tmp_path / "keys.json"),
    )
    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    r = client.post("/api/ask", json={"user": "gokul", "question": "a" * 200})
    assert r.status_code == 400


def test_rate_limit_triggers(tmp_path: Path):
    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="disabled",
        ATLAS_RATE_LIMIT_PER_MINUTE="2",
        ATLAS_API_KEYS_FILE=str(tmp_path / "keys.json"),
    )
    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    payload = {"user": "gokul", "question": "top drivers"}
    statuses = [client.post("/api/ask", json=payload).status_code for _ in range(6)]
    assert 429 in statuses


def test_disabled_mode_get_audit_is_open(tmp_path: Path):
    """In disabled mode, GET audit is anonymous — for backward compat."""

    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="disabled",
        ATLAS_API_KEYS_FILE=str(tmp_path / "keys.json"),
    )
    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    r = client.get("/api/audit")
    assert r.status_code == 200


def test_enforced_audit_requires_role(tmp_path: Path):
    keys_path = tmp_path / "api_keys.json"
    module = _fresh_app_with_env(
        ATLAS_AUTH_MODE="enforced",
        ATLAS_API_KEYS_FILE=str(keys_path),
        ATLAS_KEY_PEPPER="unit-test",
    )
    from atlas.api.auth import hash_key, mint_key
    plain_key = mint_key()
    audit_key = mint_key()
    keys_path.write_text(json.dumps({
        "keys": [
            {"hash": hash_key(plain_key, "unit-test"), "user_id": "gokul",
             "roles": ["engineering"], "tenant_id": "default", "display_name": "Gokul", "disabled": False},
            {"hash": hash_key(audit_key, "unit-test"), "user_id": "auditor",
             "roles": ["audit"], "tenant_id": "default", "display_name": "Auditor", "disabled": False},
        ]
    }))

    from fastapi.testclient import TestClient
    client = TestClient(module.app)
    assert client.get("/api/audit", headers={"X-Atlas-Key": plain_key}).status_code == 403
    assert client.get("/api/audit", headers={"X-Atlas-Key": audit_key}).status_code == 200
