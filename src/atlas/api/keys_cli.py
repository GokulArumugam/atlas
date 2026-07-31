"""CLI for minting API keys and updating the on-disk key store.

Usage:
    atlas-mint-key --user gokul --roles engineering --tenant acme
        Mints a new key, appends the hashed entry to `data/api_keys.json`,
        and prints the plaintext key to stdout. Store the key immediately —
        it can never be recovered from the store.

    atlas-mint-key --list
        Lists identities in the store (never the plaintext keys).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from atlas.api.auth import hash_key, mint_key
from atlas.config.settings import get_settings


def _load(path: Path) -> dict:
    if not path.exists():
        return {"keys": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"keys": []}
    if not isinstance(raw, dict) or "keys" not in raw:
        return {"keys": []}
    return raw


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atlas-mint-key")
    parser.add_argument("--user", help="user_id the key resolves to")
    parser.add_argument("--roles", default="", help="comma-separated roles")
    parser.add_argument("--tenant", default="default", help="tenant_id (default 'default')")
    parser.add_argument("--display-name", default="", help="human-readable name")
    parser.add_argument("--list", action="store_true", help="list identities in the store")
    parser.add_argument("--path", default=None, help="override key store path")
    args = parser.parse_args(argv)

    settings = get_settings()
    path = Path(args.path) if args.path else settings.api_keys_path

    if args.list:
        data = _load(path)
        for entry in data.get("keys", []):
            print(
                f"user_id={entry.get('user_id')} "
                f"roles={entry.get('roles')} tenant={entry.get('tenant_id')} "
                f"disabled={entry.get('disabled', False)}"
            )
        return 0

    if not args.user:
        parser.error("--user is required unless --list")

    key = mint_key()
    entry = {
        "hash": hash_key(key),
        "user_id": args.user,
        "roles": [r.strip() for r in args.roles.split(",") if r.strip()],
        "tenant_id": args.tenant,
        "display_name": args.display_name or args.user,
        "disabled": False,
    }
    data = _load(path)
    data.setdefault("keys", []).append(entry)
    _save(path, data)

    print(key)
    print(
        f"# stored at {path}. Copy the line above — it will not be shown again.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
