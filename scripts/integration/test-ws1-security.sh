#!/usr/bin/env bash
# WS1 hardened smoke test: runs the production Atlas image in a hardened
# Podman container and exercises the security surface end-to-end.
#
# Exit non-zero on any failure. Intended for local dev and CI.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${ATLAS_IMAGE:-atlas:prod}"
NAME="atlas-ws1-smoke"

cd "$REPO_ROOT"

echo "==> Building $IMAGE"
podman build -t "$IMAGE" .

echo "==> Cleaning previous container"
podman rm -f "$NAME" >/dev/null 2>&1 || true

echo "==> Starting hardened container"
podman run -d --name "$NAME" \
  --tmpfs /app/data:rw,mode=0777 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  -e ATLAS_AUTH_MODE=enforced \
  -e ATLAS_KEY_PEPPER=ws1-smoke \
  "$IMAGE" >/dev/null

trap 'podman rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

# Wait for warehouse generation + uvicorn readiness. Poll /api/health.
echo "==> Waiting for readiness"
for i in $(seq 1 30); do
  if podman exec "$NAME" python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health').status" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

exec_py() {
  podman exec "$1" python -c "$2"
}

check() {
  local label="$1"; shift
  echo "-- $label"
  eval "$@"
}

KEY=$(podman exec "$NAME" atlas-mint-key --user gokul --roles engineering 2>/dev/null | head -1)
AKEY=$(podman exec "$NAME" atlas-mint-key --user auditor --roles audit 2>/dev/null | head -1)

check "health public" 'exec_py "$NAME" "import urllib.request as u; print(u.urlopen(\"http://127.0.0.1:8000/api/health\").status)"'

check "auth enforced (401 without key)" 'exec_py "$NAME" "
import urllib.request as u, urllib.error, json
req = u.Request(\"http://127.0.0.1:8000/api/ask\", data=json.dumps({\"question\":\"x\"}).encode(), headers={\"Content-Type\":\"application/json\"})
try: u.urlopen(req); print(\"FAIL\")
except urllib.error.HTTPError as e: print(\"ok\", e.code)
"'

podman exec -e KEY="$KEY" "$NAME" python -c "
import urllib.request as u, os, json
req = u.Request('http://127.0.0.1:8000/api/ask', data=json.dumps({'question':'average salary by department'}).encode(), headers={'Content-Type':'application/json','X-Atlas-Key':os.environ['KEY']})
d = json.loads(u.urlopen(req).read())
assert d['decision'] == 'deny', d
print('-- policy deny ok:', d['decision'])
"

podman exec -e KEY="$KEY" "$NAME" python -c "
import urllib.request as u, os, json
req = u.Request('http://127.0.0.1:8000/api/ask', data=json.dumps({'question':'show riders phone numbers'}).encode(), headers={'Content-Type':'application/json','X-Atlas-Key':os.environ['KEY']})
d = json.loads(u.urlopen(req).read())
assert d['decision'] == 'mask', d
assert all(v == '***MASKED***' for row in d['rows'] for v in row), d['rows'][:3]
print('-- masking ok:', d['decision'])
"

podman exec -e KEY="$KEY" "$NAME" python -c "
import urllib.request as u, urllib.error, os
req = u.Request('http://127.0.0.1:8000/api/audit', headers={'X-Atlas-Key':os.environ['KEY']})
try: u.urlopen(req); print('FAIL')
except urllib.error.HTTPError as e:
  assert e.code == 403, e.code
  print('-- audit role gate ok:', e.code)
"

podman exec -e AKEY="$AKEY" "$NAME" python -c "
import urllib.request as u, os, json
req = u.Request('http://127.0.0.1:8000/api/audit', headers={'X-Atlas-Key':os.environ['AKEY']})
d = json.loads(u.urlopen(req).read())
assert d['chain_ok'] is True, d
print('-- auditor chain_ok ok')
"

podman exec "$NAME" python -c "
import urllib.request as u
r = u.urlopen('http://127.0.0.1:8000/api/health')
required = ['Content-Security-Policy','X-Content-Type-Options','X-Frame-Options','X-Request-ID']
for h in required:
  assert r.headers.get(h), f'missing {h}'
print('-- security headers ok:', required)
"

podman exec -e KEY="$KEY" "$NAME" python -c "
import urllib.request as u, urllib.error, os, json
codes=[]
for _ in range(45):
  req = u.Request('http://127.0.0.1:8000/api/ask', data=json.dumps({'question':'top drivers'}).encode(), headers={'Content-Type':'application/json','X-Atlas-Key':os.environ['KEY']})
  try: codes.append(u.urlopen(req).status)
  except urllib.error.HTTPError as e: codes.append(e.code)
assert 429 in codes, codes
print('-- rate limit ok:', codes.count(429), 'requests rate-limited')
"

podman exec "$NAME" id | grep -q "uid=10001" && echo "-- non-root ok" || (echo "FAIL: running as root"; exit 1)

echo "==> All WS1 checks passed."
