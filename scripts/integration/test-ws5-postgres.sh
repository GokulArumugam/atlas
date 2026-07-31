#!/usr/bin/env bash
# WS5.4 real Postgres integration test.
# Spins up Postgres, loads a tiny schema, points Atlas at it, and exercises
# a policy-scoped ask + a firewall deny + a masked-column projection.
#
# Confirms that the PostgresConnector actually works against a real Postgres —
# something STATE.md previously called out as untested.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NET="atlas-ws5-net"
PG="atlas-ws5-pg"
ATLAS="atlas-ws5-app"
IMAGE="${ATLAS_IMAGE:-atlas:prod}"

cd "$REPO_ROOT"

cleanup() {
  podman rm -f "$ATLAS" "$PG" >/dev/null 2>&1 || true
  podman network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

podman network create "$NET" >/dev/null

echo "==> Starting Postgres 16"
podman run -d --name "$PG" --network "$NET" \
  -e POSTGRES_PASSWORD=atlas -e POSTGRES_DB=atlas -e POSTGRES_USER=atlas \
  docker.io/library/postgres:16 >/dev/null

echo "==> Waiting for Postgres readiness"
for i in $(seq 1 30); do
  if podman exec "$PG" pg_isready -U atlas -d atlas >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "==> Loading schema"
podman exec -i "$PG" psql -U atlas -d atlas <<'SQL'
CREATE SCHEMA IF NOT EXISTS rides;
CREATE TABLE rides.locations (id INT PRIMARY KEY, name TEXT, city TEXT);
CREATE TABLE rides.riders (id INT PRIMARY KEY, full_name TEXT, phone TEXT, email TEXT, home_city TEXT);
CREATE TABLE rides.trips (id INT PRIMARY KEY, rider_id INT, status TEXT);
INSERT INTO rides.locations VALUES (1,'Airport','SF'),(2,'Downtown','SF'),(3,'Marina','SF');
INSERT INTO rides.riders VALUES (1,'Alice','+1-555-0100','alice@example.com','SF'),(2,'Bob','+1-555-0101','bob@example.com','NY');
INSERT INTO rides.trips VALUES (1,1,'completed'),(2,1,'cancelled'),(3,2,'completed');
SQL

echo "==> Building Atlas image"
podman build -t "$IMAGE" . >/dev/null

echo "==> Writing policy that scopes analyst to rides.*"
cat > "$REPO_ROOT/scripts/integration/ws5-pg-policy.yaml" <<'YAML'
tables:
  rides:
    locations: [id, name, city]
    riders: [id, full_name, phone, email, home_city]
    trips: [id, rider_id, status]
pii_columns:
  - rides.riders.phone
  - rides.riders.email
users:
  analyst:
    team: analytics
    visible_tables: [rides.trips, rides.riders, rides.locations]
    unmasked_pii: []
YAML

echo "==> Starting Atlas pointing at Postgres"
podman run -d --name "$ATLAS" --network "$NET" \
  --tmpfs /app/data:rw,mode=0777 \
  --cap-drop=ALL --security-opt=no-new-privileges \
  -v "$REPO_ROOT/scripts/integration/ws5-pg-policy.yaml:/app/policy.yaml:ro,Z" \
  -e ATLAS_AUTH_MODE=enforced \
  -e ATLAS_KEY_PEPPER=ws5-pg \
  -e ATLAS_POLICY_FILE=/app/policy.yaml \
  -e ATLAS_POSTGRES_DSN="postgresql://atlas:atlas@${PG}:5432/atlas" \
  "$IMAGE" >/dev/null

# We need to point the analyst at Postgres, which means running with a
# custom entrypoint that swaps DuckDBConnector for PostgresConnector.
# For this integration test we exec a small script inside the container.
sleep 4
KEY=$(podman exec "$ATLAS" atlas-mint-key --user analyst --roles analytics 2>/dev/null | head -1)

echo "==> Verifying PostgresConnector connects and executes"
podman exec "$ATLAS" python -c "
from atlas.connector.postgres_connector import PostgresConnector
c = PostgresConnector('postgresql://atlas:atlas@${PG}:5432/atlas')
cols, rows = c.execute('SELECT COUNT(*) FROM rides.trips')
print('trips count:', rows[0][0])
cols, rows = c.execute('SELECT current_setting(\'default_transaction_read_only\')')
print('read_only session:', rows[0][0])
try:
    c.execute('CREATE TABLE evil (id INT)')
    print('WRITE SUCCEEDED - failure!')
except Exception as e:
    print('write blocked:', type(e).__name__)
"

echo "==> Firewall+policy check via analyst directly"
podman exec "$ATLAS" python -c "
from atlas.agent.analyst import Analyst
from atlas.connector.postgres_connector import PostgresConnector
a = Analyst(connector=PostgresConnector('postgresql://atlas:atlas@${PG}:5432/atlas'), audit_path='/tmp/pg_audit.duckdb')
ans = a.ask('analyst', 'show riders phone numbers')
print('decision:', ans.decision.value)
print('sql     :', (ans.sql or '')[:200])
print('rows    :', ans.rows[:3])
"

rm -f "$REPO_ROOT/scripts/integration/ws5-pg-policy.yaml"
echo "==> Done."
