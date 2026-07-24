# Governed AI Data Analyst — state

## Built

- A deterministic synthetic DuckDB warehouse generator at `src/atlas/data/generate.py`.
- Business schemas `rides` and `hr`, plus `meta.query_history` containing realistic historical SQL with real foreign-key joins.
- A dependency-free, fail-closed policy engine at `src/atlas/policy`.
- Policy tests in `tests/test_policy.py`, including catalog leak prevention.
- The SQLGlot-based, fail-closed SQL firewall at
  `src/atlas/firewall/firewall.py`, with adversarial and real-DuckDB
  execution coverage in `tests/test_firewall.py` and
  `tests/test_firewall_execution.py`.
- A read-only DuckDB catalog at `src/atlas/catalog/catalog.py`.
  It introspects real table types and row counts from `information_schema`, adds
  human-approved semantic notes from `docs/semantic`, and never samples PII:
  `column_stats` returns only `{'masked': True}` for a PII column.
- A query-history-backed mind map at `src/atlas/catalog/mindmap.py`.
  It ranks joins mined from `meta.query_history` ahead of declared foreign keys
  and compatible `<table>_id -> <table>.id` heuristics, and renders a scoped
  Mermaid flowchart for a user.
- A deterministic retrieval context builder at
  `src/atlas/catalog/context.py`. It selects relevant visible tables
  using token overlap with schema and semantic notes, includes only connecting
  visible edges, and removes semantic-note lines that name hidden tables.
- Catalog and mind-map regression tests in `tests/test_catalog.py` and
  `tests/test_mindmap.py`.
- The runtime analyst in `src/atlas/agent`. It builds a scoped
  context, uses Claude only when `ANTHROPIC_API_KEY` is configured (and otherwise
  uses an offline deterministic demo generator), sends generated SQL through the
  firewall, executes the approved SQL with a read-only warehouse connection, and
  chooses a small deterministic chart specification.
- A tamper-evident audit log in `src/atlas/audit/audit.py`. It writes
  every allow, mask, and deny to `data/audit.duckdb`, the customer's own audit
  store, not `data/warehouse.duckdb`. Keeping the write-capable audit database
  separate avoids DuckDB writer-lock conflicts with the read-only warehouse and
  represents the audit store that remains in the customer's environment.

## Run commands

```bash
cd atlas
./.venv/bin/python -m src.atlas.data.generate
./.venv/bin/pytest tests/ -q
./.venv/bin/python -c "import duckdb; c=duckdb.connect('data/warehouse.duckdb', read_only=True); print(c.execute('SELECT COUNT(DISTINCT sql_text) FROM meta.query_history').fetchone()[0])"
./.venv/bin/python -c "import duckdb; c=duckdb.connect('data/warehouse.duckdb'); print(c.execute(\"SELECT AVG(t.rider_count), COUNT(*) FROM rides.trips t JOIN rides.locations s ON t.start_location_id=s.id JOIN rides.locations d ON t.end_location_id=d.id WHERE s.name='Airport' AND d.name='Downtown'\").fetchone())"
```

The sanity query must return a non-null average and a positive trip count.

### Runtime demo

```bash
cd atlas
PYTHONPATH=src ./.venv/bin/python scripts/demo.py
```

**Runtime security invariant:** generated SQL is never executed directly. The
only call to the warehouse is `connection.execute(firewall_result.safe_sql)`
after a non-deny firewall verdict; denials never open a warehouse connection.
The raw generated SQL is retained solely in the customer audit record.

### Catalog and mind map

Construct the metadata map and a user-specific Mermaid view with:

```bash
cd atlas
PYTHONPATH=src ./.venv/bin/python -c "from atlas.catalog.catalog import Catalog; from atlas.catalog.mindmap import MindMap; from atlas.policy.engine import PolicyEngine; c=Catalog('data/warehouse.duckdb', PolicyEngine()); m=MindMap(c); m.build('data/warehouse.duckdb'); print(m.to_mermaid('gokul'))"
```

`Catalog.tables_for(user)`, `MindMap.edges_for(user)`,
`MindMap.subgraph_for(user)`, `MindMap.to_mermaid(user)`, and `build_context`
all enforce the same fail-closed boundary: unknown users receive no tables or
edges, and every returned edge has both endpoints in `PolicyEngine.visible_tables(user)`.
The context builder additionally scopes semantic notes, so notes on a visible
fact table cannot disclose the name of a hidden dimension table.

## SQL firewall

`SqlFirewall.check(user, sql)` is the execution boundary: it returns either a
normalised, fully-qualified `safe_sql` or a denial with `safe_sql=None`. It
builds a SQLGlot `MappingSchema` from the approved policy catalog, expands
`SELECT *`, and traces qualified columns through CTEs, subqueries, aliases,
joins, and set operations to concrete catalog tables.

It enforces these eight rules:

1. Only a pure `SELECT` (including `WITH ... SELECT`) is accepted; DML, DDL,
   commands, attachment, pragmas, settings, and `SELECT ... INTO` are denied.
2. Exactly one parsed statement is accepted.
3. Parse failures never pass through.
4. Any unresolved or ambiguous table/column lineage is denied rather than
   guessed.
5. Every concrete table is checked against `PolicyEngine.visible_tables`; table
   refusals use `PolicyEngine.check_table(...).reason` unchanged so table names
   cannot be enumerated through error wording.
6. A column the policy marks `MASK` is permitted inside `COUNT`, `SUM`, `AVG`,
   `MIN`, or `MAX`; it is masked in final output projections; and it is denied
   in filtering, grouping, ordering, joins, or HAVING predicates because those
   are inference channels.
7. Table-free reads such as `SELECT 1` are allowed.
8. Every unexpected firewall error becomes a generic denial. Nothing executable
   is returned for a denial.

### Masking design

The policy contract supplies the masking expression (`'***MASKED***'` in this
demo). The firewall replaces the entire final projection expression, retaining
its output alias, rather than substituting inside an aggregate argument. That
means `AVG(salary)` stays numeric and executable for a role permitted to use it,
while an individual protected value becomes a valid string output. The current
policy has no numeric column that is `MASK` for a user; if a customer adds one,
the supplied masking expression must be type-compatible with any surrounding
set-operation contract. Aggregate inputs are deliberately never rewritten, so
numeric aggregates remain valid and fulfil the promise that protected values can
still be counted or averaged.

Run the complete verification suite with:

```bash
cd atlas
./.venv/bin/pytest tests/ -q
```

## Public policy contract

- `TableRef(schema, table)` and `ColumnRef(schema, table, column)` stringify as fully-qualified names.
- `Decision` is `ALLOW`, `DENY`, or `MASK`; `Verdict` exposes `decision`, `reason`, `masked_columns`, `denied_tables`, and `denied_columns`.
- `PolicyEngine` exposes `users`, `team_of`, `visible_tables`, `check_table`, `check_column`, `masking_expr`, and `is_pii` with the signatures in the task specification.
- Unknown users, unknown tables, and unknown columns are denied. `visible_tables` is the catalog boundary: Gokul has rides only; HR does not appear there.

## Demo API and web UI

The project is now feature-complete for the 90-second demo. Run the local
server from the workspace root:

```bash
cd atlas
./.venv/bin/python scripts/serve.py
```

Open `http://127.0.0.1:8000`. `scripts/serve.py` inserts `src` on the import
path and runs Uvicorn on port 8000.

The FastAPI surface in `src/atlas/api/app.py` is deliberately thin:

- `GET /api/users` returns the four demo users and their teams.
- `POST /api/ask` accepts `{"user": ..., "question": ...}` and returns the
  governed analyst answer (including the policy decision, approved SQL only,
  masked columns, chart spec, rows, and timing).
- `GET /api/graph/{user}` returns a policy-scoped join graph. Hidden tables are
  excluded from both nodes and edges.
- `GET /api/audit` verifies the customer-owned hash chain and returns newest
  audit entries first.
- `GET /` serves the single-page UI; `/static` serves its local CSS and JS.

`Analyst`, its `Catalog`, and its query-history-backed `MindMap` are constructed
once when the API application loads, not per request. All handlers turn an
unexpected failure into a small JSON error response.

The fully self-contained interface lives in `static`: `index.html` is the
shell, `app.js` provides vanilla-JS chat, chart SVGs, user switching, scoped
join-map SVGs, and the auditor console, and `style.css` is the responsive dark
product presentation. It makes no external network calls or dependency loads.
Switching to `auditor` replaces chat with the hash-chain status banner and the
complete audit table; all other users see their own scoped map and governed chat.

## Gotchas

- The generator recreates the three owned schemas on every run; it does not delete the database file.
- Random values use seed 42. Dates are relative to the day the generator runs so trips always cover the preceding 120 days.
- DuckDB is the only non-stdlib dependency used by the generator; no package installs are required.
