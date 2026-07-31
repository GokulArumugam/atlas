"""Snowflake warehouse connector.

Requires:
    pip install "atlas-analyst[snowflake]"

Reads credentials from a ``SnowflakeConfig`` — either passed explicitly or
built from env vars (``SNOWFLAKE_ACCOUNT``, ``SNOWFLAKE_USER``,
``SNOWFLAKE_PASSWORD`` or ``SNOWFLAKE_PRIVATE_KEY``, ``SNOWFLAKE_WAREHOUSE``,
``SNOWFLAKE_DATABASE``, ``SNOWFLAKE_SCHEMA``). Sessions are read-only via
``SET STATEMENT_TIMEOUT`` and role-level SELECT-only grants (recommended).

This is a portfolio-quality stub: it will connect and execute against a real
Snowflake, but production hardening (key-pair rotation, oauth, private-link)
is left to operators.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from atlas.connector.base import ForeignKeyInfo, WarehouseConnector


@dataclass(frozen=True)
class SnowflakeConfig:
    account: str
    user: str
    password: str | None = None
    private_key: str | None = None
    warehouse: str | None = None
    database: str | None = None
    schema: str | None = None
    role: str | None = None

    @classmethod
    def from_env(cls) -> "SnowflakeConfig":
        return cls(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ.get("SNOWFLAKE_PASSWORD"),
            private_key=os.environ.get("SNOWFLAKE_PRIVATE_KEY"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
            database=os.environ.get("SNOWFLAKE_DATABASE"),
            schema=os.environ.get("SNOWFLAKE_SCHEMA"),
            role=os.environ.get("SNOWFLAKE_ROLE"),
        )


class SnowflakeConnector(WarehouseConnector):
    def __init__(self, config: SnowflakeConfig, statement_timeout_seconds: int = 60) -> None:
        try:
            import snowflake.connector  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "snowflake-connector-python is required. "
                'Install with: pip install "atlas-analyst[snowflake]"'
            ) from exc
        conn_kwargs: dict[str, Any] = {"account": config.account, "user": config.user}
        if config.password:
            conn_kwargs["password"] = config.password
        elif config.private_key:
            conn_kwargs["private_key"] = config.private_key
        else:
            raise ValueError("Snowflake config requires password or private_key.")
        for k in ("warehouse", "database", "schema", "role"):
            v = getattr(config, k)
            if v:
                conn_kwargs[k] = v
        self._conn = snowflake.connector.connect(**conn_kwargs)
        with self._conn.cursor() as cur:
            cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(statement_timeout_seconds)}")

    @property
    def dialect(self) -> str:
        return "snowflake"

    def execute(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params or None)
            columns = [d[0].lower() for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
            return columns, rows

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        # Snowflake doesn't publish FKs consistently; return empty and let the
        # MindMap fall back to query-history mining.
        return []

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
