#!/usr/bin/env bash
# WS3 Ollama integration: spins up an Ollama sidecar with llama3.2:1b
# (small enough for laptops), configures Atlas to use it, and runs an
# end-to-end ask through the local model. Slow first run (model pull),
# fast subsequent runs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${ATLAS_IMAGE:-atlas:prod}"
OLLAMA_IMAGE="${OLLAMA_IMAGE:-docker.io/ollama/ollama:latest}"
MODEL="${ATLAS_OLLAMA_MODEL:-llama3.2:1b}"
NET="atlas-ws3-net"
OLLAMA="atlas-ws3-ollama"
ATLAS="atlas-ws3-app"

cd "$REPO_ROOT"

cleanup() {
  podman rm -f "$ATLAS" "$OLLAMA" >/dev/null 2>&1 || true
  podman network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup
podman network create "$NET" >/dev/null

echo "==> Starting Ollama sidecar"
podman run -d --name "$OLLAMA" --network "$NET" "$OLLAMA_IMAGE" >/dev/null

echo "==> Pulling model $MODEL (this can take a few minutes on first run)"
for i in $(seq 1 60); do
  if podman exec "$OLLAMA" ollama list >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
podman exec "$OLLAMA" ollama pull "$MODEL"

echo "==> Building Atlas image"
podman build -t "$IMAGE" . >/dev/null

echo "==> Starting Atlas pointing at Ollama"
podman run -d --name "$ATLAS" --network "$NET" \
  --tmpfs /app/data:rw,mode=0777 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  -e ATLAS_AUTH_MODE=enforced \
  -e ATLAS_KEY_PEPPER=ws3-ollama \
  -e ATLAS_USE_OLLAMA_BY_DEFAULT=1 \
  -e ATLAS_OLLAMA_BASE_URL="http://${OLLAMA}:11434" \
  "$IMAGE" >/dev/null

for i in $(seq 1 30); do
  if podman exec "$ATLAS" python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

KEY=$(podman exec "$ATLAS" atlas-mint-key --user gokul --roles engineering,admin 2>/dev/null | head -1)

echo "==> Asking Atlas through Ollama"
podman exec -e KEY="$KEY" "$ATLAS" python -c "
import os, json, urllib.request as u, urllib.error
req = u.Request('http://127.0.0.1:8000/api/ask', data=json.dumps({'question':'top drivers'}).encode(), headers={'Content-Type':'application/json','X-Atlas-Key':os.environ['KEY']})
try:
    d = json.loads(u.urlopen(req, timeout=180).read())
    print('decision:', d['decision'])
    print('reason  :', d['reason'][:100])
    print('sql     :', (d.get('sql') or '')[:200])
    print('rows    :', len(d.get('rows', [])))
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read().decode())
"

echo "==> Done."
