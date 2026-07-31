FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer-cached separately from source code).
COPY pyproject.toml README.md LICENSE ./
RUN pip install --no-cache-dir .

# Copy application source after deps so code changes don't bust the dep layer.
COPY src/ src/
COPY static/ static/
COPY docs/ docs/
COPY scripts/ scripts/

# data/ is gitignored and generated at runtime; create the dir so the
# warehouse generator and analyst can write there without errors.
RUN mkdir -p data

EXPOSE 8000

# On first start: generate the synthetic demo warehouse if it does not yet
# exist, then launch the API on all interfaces so Docker port-mapping works.
# ATLAS_HOST / ATLAS_PORT can be overridden via docker run -e or compose env.
CMD ["sh", "-c", \
  "[ ! -f data/warehouse.duckdb ] && atlas-generate; \
   uvicorn atlas.api.app:app \
     --host ${ATLAS_HOST:-0.0.0.0} \
     --port ${ATLAS_PORT:-8000}"]
