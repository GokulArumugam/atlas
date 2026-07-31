"""Best-effort OpenTelemetry integration.

Zero hard dependency. If `opentelemetry-api` isn't installed or
`ATLAS_OTEL_ENABLED` isn't set, all tracing calls become no-ops.

Set `ATLAS_OTEL_ENABLED=1` and install `opentelemetry-api opentelemetry-sdk
opentelemetry-exporter-otlp` to activate. Point `OTEL_EXPORTER_OTLP_ENDPOINT`
at your collector.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_status(self, *_: Any, **__: Any) -> None:
        return None

    def end(self) -> None:
        return None

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


class _NoopTracer:
    @contextlib.contextmanager
    def start_as_current_span(self, name: str) -> Iterator[_NoopSpan]:
        yield _NoopSpan()


def _enabled() -> bool:
    return os.environ.get("ATLAS_OTEL_ENABLED", "").strip().lower() in {"1", "true", "yes"}


_tracer: Any | None = None


def get_tracer() -> Any:
    global _tracer
    if _tracer is not None:
        return _tracer
    if not _enabled():
        _tracer = _NoopTracer()
        return _tracer
    try:
        from opentelemetry import trace  # type: ignore
        _tracer = trace.get_tracer("atlas")
    except ImportError:
        _tracer = _NoopTracer()
    return _tracer
