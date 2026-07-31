FROM python:3.11-slim AS build

WORKDIR /app

# Build layer: install deps into a virtualenv we can copy into the runtime.
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir '.[postgres]'


FROM python:3.11-slim AS runtime

# Non-root user for the whole runtime.
RUN useradd --system --create-home --home-dir /home/atlas --uid 10001 --user-group atlas

WORKDIR /app
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ATLAS_ROOT=/app \
    ATLAS_HOST=0.0.0.0 \
    ATLAS_PORT=8000 \
    ATLAS_OLLAMA_BASE_URL=http://host.containers.internal:11434

COPY --from=build /opt/venv /opt/venv
COPY src/ src/
COPY static/ static/
COPY docs/ docs/
COPY scripts/ scripts/

# `data/` is a mount point at runtime; make sure it exists and is writable.
RUN mkdir -p data && chown -R atlas:atlas /app

USER atlas

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('ATLAS_PORT','8000')+'/api/health', timeout=3).status==200 else 1)"

# First-start bootstrap: generate the synthetic demo warehouse if missing.
CMD ["sh", "-c", "\
  if [ ! -f data/warehouse.duckdb ]; then atlas-generate; fi; \
  exec uvicorn atlas.api.app:app --host \"${ATLAS_HOST}\" --port \"${ATLAS_PORT}\"\
"]
