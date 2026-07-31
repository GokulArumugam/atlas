#!/usr/bin/env bash
# WS2 efficiency benchmark: measures p50/p95/p99 latency under concurrent
# load against the hardened Atlas container. Uses stdlib in Python — no
# `hey` install required — so this runs anywhere Podman does.
#
# Prints a small table of latency stats + cache hit ratio.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="${ATLAS_IMAGE:-atlas:prod}"
NAME="atlas-ws2-bench"
REQUESTS="${ATLAS_BENCH_REQUESTS:-200}"
CONCURRENCY="${ATLAS_BENCH_CONCURRENCY:-16}"

cd "$REPO_ROOT"

echo "==> Building $IMAGE"
podman build -t "$IMAGE" . >/dev/null

echo "==> Cleaning previous container"
podman rm -f "$NAME" >/dev/null 2>&1 || true

echo "==> Starting hardened container"
podman run -d --name "$NAME" \
  --tmpfs /app/data:rw,mode=0777 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  -e ATLAS_AUTH_MODE=enforced \
  -e ATLAS_KEY_PEPPER=ws2-bench \
  -e ATLAS_RATE_LIMIT_PER_MINUTE=10000 \
  "$IMAGE" >/dev/null

trap 'podman rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

echo "==> Waiting for readiness"
for i in $(seq 1 30); do
  if podman exec "$NAME" python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

KEY=$(podman exec "$NAME" atlas-mint-key --user gokul --roles engineering,admin 2>/dev/null | head -1)

echo "==> Running $REQUESTS requests at concurrency $CONCURRENCY"
podman exec -e KEY="$KEY" -e REQUESTS="$REQUESTS" -e CONCURRENCY="$CONCURRENCY" "$NAME" python -c '
import os, json, time, concurrent.futures as cf, urllib.request as u
KEY = os.environ["KEY"]
N = int(os.environ["REQUESTS"])
C = int(os.environ["CONCURRENCY"])
def one():
    body = json.dumps({"question":"top drivers"}).encode()
    req = u.Request("http://127.0.0.1:8000/api/ask", data=body, headers={"Content-Type":"application/json","X-Atlas-Key":KEY})
    t0 = time.perf_counter()
    try:
        r = u.urlopen(req); r.read(); status = r.status
    except Exception as e:
        status = getattr(e, "code", 599)
    return status, (time.perf_counter()-t0)*1000
start = time.perf_counter()
with cf.ThreadPoolExecutor(max_workers=C) as ex:
    results = list(ex.map(lambda _: one(), range(N)))
elapsed = time.perf_counter() - start
statuses = [s for s,_ in results]
latencies = sorted([l for _,l in results])
def pct(v,p):
    k = int(round((p/100)*(len(v)-1))); return v[k]
print(f"total_requests    : {N}")
print(f"concurrency       : {C}")
print(f"wall_seconds      : {elapsed:.2f}")
print(f"rps               : {N/elapsed:.1f}")
print(f"status_counts     : {dict((s,statuses.count(s)) for s in set(statuses))}")
print(f"p50 latency (ms)  : {pct(latencies,50):.1f}")
print(f"p90 latency (ms)  : {pct(latencies,90):.1f}")
print(f"p95 latency (ms)  : {pct(latencies,95):.1f}")
print(f"p99 latency (ms)  : {pct(latencies,99):.1f}")
print(f"max latency (ms)  : {max(latencies):.1f}")
'

echo "==> Cache stats (from /api/metrics)"
podman exec -e KEY="$KEY" "$NAME" python -c '
import os, json, urllib.request as u
req = u.Request("http://127.0.0.1:8000/api/metrics", headers={"X-Atlas-Key":os.environ["KEY"]})
print(json.dumps(json.loads(u.urlopen(req).read()), indent=2))
'

echo "==> Done."
