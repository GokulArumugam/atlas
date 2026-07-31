"""BigQuery warehouse connector.

Requires:
    pip install "atlas-analyst[bigquery]"

Uses Google Application Default Credentials by default; pass explicit
``credentials_json`` string for service-account use. A query-scan cap in
bytes is enforced via ``maximumBytesBilled`` on every job — the primary cost
guardrail. Set via ``ATLAS_BIGQUERY_MAX_BYTES_BILLED`` or the constructor.
"""

from __future__ import annotations

import json
import os
from typing import Any

from atlas.connector.base import ForeignKeyInfo, WarehouseConnector


class BigQueryConnector(WarehouseConnector):
    def __init__(
        self,
        project: str | None = None,
        credentials_json: str | None = None,
        maximum_bytes_billed: int | None = None,
    ) -> None:
        try:
            from google.cloud import bigquery  # type: ignore
            from google.oauth2 import service_account  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "google-cloud-bigquery is required. "
                'Install with: pip install "atlas-analyst[bigquery]"'
            ) from exc

        creds = None
        if credentials_json:
            info = json.loads(credentials_json)
            creds = service_account.Credentials.from_service_account_info(info)
        self._client = bigquery.Client(project=project, credentials=creds)
        self._max_bytes = (
            maximum_bytes_billed
            if maximum_bytes_billed is not None
            else int(os.environ.get("ATLAS_BIGQUERY_MAX_BYTES_BILLED", "1073741824"))  # 1 GiB
        )

    @property
    def dialect(self) -> str:
        return "bigquery"

    def execute(self, sql: str, params: list[Any] | None = None) -> tuple[list[str], list[tuple]]:
        from google.cloud import bigquery  # type: ignore
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=self._max_bytes,
            use_legacy_sql=False,
        )
        job = self._client.query(sql, job_config=job_config)
        rows_iter = job.result()
        columns = [f.name for f in rows_iter.schema]
        rows = [tuple(row.values()) for row in rows_iter]
        return columns, rows

    def foreign_keys(self) -> list[ForeignKeyInfo]:
        return []

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
