#!/usr/bin/env bash
# ============================================================
# Atlas with Ollama (all-in-Podman, zero host installs).
#
# Runs two containers on a shared Podman network:
#   * atlas-ui-ollama : Ollama server (models pulled on first run)
#   * atlas-ui        : Atlas, pointing at the Ollama container
#
# UI at http://localhost:8000/. First run pulls the model, which takes
# a few minutes and a few GB. Subsequent runs are instant.
# ============================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${ATLAS_IMAGE:-atlas:prod}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-docker.io/ollama/ollama:latest}"
MODEL="${ATLAS_OLLAMA_MODEL:-qwen2.5-coder:1.5b}"
NET="atlas-ui-net"
OLLAMA="atlas-ui-ollama"
ATLAS="atlas-ui"

cd "$REPO_ROOT"

echo "==> Ensuring network"
podman network exists "$NET" 2>/dev/null || podman network create "$NET" >/dev/null

# --- Ollama sidecar ---------------------------------------------------------
if ! podman ps --format '{{.Names}}' | grep -q "^${OLLAMA}$"; then
  echo "==> Starting Ollama"
  podman rm -f "$OLLAMA" >/dev/null 2>&1 || true
  # Persist model cache across restarts via a named volume
  podman volume exists ollama-models 2>/dev/null || podman volume create ollama-models >/dev/null
  podman run -d --name "$OLLAMA" --network "$NET" \
    -v ollama-models:/root/.ollama \
    "$OLLAMA_IMAGE" >/dev/null
fi

echo "==> Waiting for Ollama"
for i in $(seq 1 60); do
  if podman exec "$OLLAMA" ollama list >/dev/null 2>&1; then break; fi
  sleep 2
done

# Pull the model if not already there. `ollama list` prints one line per model.
if ! podman exec "$OLLAMA" ollama list | tail -n +2 | awk '{print $1}' | grep -q "^${MODEL}$"; then
  echo "==> Pulling model ${MODEL} (this can take several minutes on first run)"
  podman exec "$OLLAMA" ollama pull "$MODEL"
fi

# --- Atlas ------------------------------------------------------------------
echo "==> Building Atlas image"
podman build -t "$IMAGE" . >/dev/null

echo "==> Starting Atlas"
podman rm -f "$ATLAS" >/dev/null 2>&1 || true
podman run -d --name "$ATLAS" --network "$NET" \
  --tmpfs /app/data:rw,mode=0777 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  -p 127.0.0.1:8000:8000 \
  -e ATLAS_AUTH_MODE=disabled \
  -e ATLAS_LOG_JSON=false \
  -e ATLAS_OLLAMA_BASE_URL="http://${OLLAMA}:11434" \
  "$IMAGE" >/dev/null

echo "==> Waiting for Atlas"
for i in $(seq 1 30); do
  if curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then break; fi
  sleep 1
done

echo ""
echo "==> Ready."
echo "    UI:      http://localhost:8000/"
echo "    Model:   ${MODEL}"
echo "    Ollama:  http://${OLLAMA}:11434 (inside container network)"
echo ""
echo "    Stop everything:  podman rm -f ${ATLAS} ${OLLAMA}"
echo "    Live Atlas logs:  podman logs -f ${ATLAS}"
echo "    Live Ollama logs: podman logs -f ${OLLAMA}"
