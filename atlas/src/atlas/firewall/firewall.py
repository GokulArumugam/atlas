"""A deliberately small, fail-closed SQL firewall.

This module is the enforcement boundary for warehouses that do not provide
their own row/column security.  It accepts only completely resolvable read
queries, checks every concrete source against the policy engine, and rewrites
only final output projections that policy says must be masked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from atlas.policy.engine import PolicyEngine
from atlas.policy.model import ColumnRef, Decision, TableRef
from atlas.policy.policies import POLICY_CONFIG


@dataclass
class FirewallResult:
    decision: Decision
    reason: str
    safe_sql: str | None
    tables_touched: list[TableRef] = field(default_factory=list)
    columns_touched: list[ColumnRef] = field(default_factory=list)
    masked_columns: list[ColumnRef] = field(default_factory=list)


class _Unresolvable(Exception):
    """Internal signal: input cannot be proven safe enough to execute."""


class SqlFirewall:
    """Parse, resolve, authorize, and (where permitted) mask a SQL query."""

    _AGGREGATES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
    _INFERENCE_CLAUSES = (exp.Where, exp.Having, exp.Group, exp.Order, exp.Join)
    _FORBIDDEN = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Merge,
        exp.Drop,
        exp.Create,
        exp.Alter,
        exp.TruncateTable,
        exp.Grant,
        exp.Attach,
        exp.Command,
        exp.Pragma,
        exp.Set,
        exp.Into,
    )

    def __init__(self, policy: PolicyEngine, dialect: str = "duckdb") -> None:
        self.policy = policy
        self.dialect = dialect
        self._catalog = {
            schema.lower(): {
                table.lower(): {column.lower() for column in columns}
                for table, columns in tables.items()
            }
            for schema, tables in POLICY_CONFIG["tables"].items()
        }
        # MappingSchema expects column -> type mappings, not the sets kept by the
        # policy catalog. UNKNOWN is sufficient for qualification and star expansion.
        mapping = {
            schema: {
                table: {column: "UNKNOWN" for column in sorted(columns)}
                for table, columns in tables.items()
            }
            for schema, tables in self._catalog.items()
        }
        self._schema = MappingSchema(mapping, dialect=dialect, normalize=True)

    def check(self, user: str, sql: str) -> FirewallResult:
        """Return executable, normalized SQL only after every safety check passes."""
        try:
            if not isinstance(sql, str) or not sql.strip():
                return self._deny("I can't run an empty or invalid SQL query.")

            statements = sqlglot.parse(sql, read=self.dialect)
            statements = [statement for statement in statements if statement is not None]
            if len(statements) != 1:
                return self._deny("I can only run one read-only SQL statement at a time.")

            parsed = statements[0]
            if not isinstance(parsed, exp.Query) or parsed.find(self._FORBIDDEN):
                return self._deny("I can only run a read-only SELECT query.")

            # Authorize explicit concrete table names before qualification. This both
            # prevents forbidden sources from being hidden in a CTE and preserves the
            # policy engine's intentionally identical real-vs-fake denial wording.
            explicit_tables = self._explicit_base_tables(parsed)
            denied = self._first_denied_table(user, explicit_tables)
            if denied is not None:
                return self._deny(denied.reason)

            qualified = qualify(
                parsed,
                schema=self._schema,
                dialect=self.dialect,
                validate_qualify_columns=True,
                identify=False,
                quote_identifiers=False,
            )
            scopes = list(traverse_scope(qualified))
            if not scopes:
                raise _Unresolvable("query has no resolvable scope")
            scope_by_expression = {id(scope.expression): scope for scope in scopes}

            # Qualification gives every ordinary column a source alias. Resolve those
            # aliases recursively through CTEs and derived tables to concrete catalog
            # columns; a gap or ambiguity is never guessed.
            columns_by_node: dict[int, set[ColumnRef]] = {}
            resolved_columns: list[ColumnRef] = []
            for scope in scopes:
                for column in scope.columns:
                    origins = self._resolve_column(scope, column, set())
                    if len(origins) != 1:
                        raise _Unresolvable("column has no single concrete source")
                    columns_by_node[id(column)] = origins
                    resolved_columns.extend(sorted(origins, key=str))

            tables_touched = self._unique(self._concrete_tables(qualified))
            # A known table could still be introduced by qualification in a form not
            # seen above; always check the final concrete table list as well.
            denied = self._first_denied_table(user, tables_touched)
            if denied is not None:
                return self._deny(denied.reason)

            columns_touched = self._unique(resolved_columns)
            protected_columns: set[ColumnRef] = set()
            for column in columns_touched:
                verdict = self.policy.check_column(user, column)
                if verdict.decision is Decision.DENY:
                    return self._deny(verdict.reason)
                # is_pii is deliberately consulted as part of the policy contract;
                # MASK, not merely PII tagging, controls this user's enforcement.
                if self.policy.is_pii(column) and verdict.decision is Decision.MASK:
                    protected_columns.add(column)

            for scope in scopes:
                for column in scope.columns:
                    hit = columns_by_node[id(column)] & protected_columns
                    if hit and self._in_inference_clause(column, scope):
                        return self._deny(self._masked_reason(hit))
                ordered_hit = self._order_uses_protected_output_alias(
                    scope, protected_columns, columns_by_node, scope_by_expression
                )
                if ordered_hit:
                    return self._deny(self._masked_reason(ordered_hit))

            masked_columns: list[ColumnRef] = []
            for output_select in self._output_selects(qualified):
                scope = scope_by_expression.get(id(output_select))
                if scope is None:
                    raise _Unresolvable("output scope could not be resolved")
                for projection in output_select.expressions:
                    origins = self._projection_origins(
                        projection, scope, columns_by_node, scope_by_expression
                    )
                    protected = origins & protected_columns
                    if not protected or self._projection_is_aggregate_only(projection, scope, scope_by_expression):
                        continue
                    self._mask_projection(projection)
                    masked_columns.extend(sorted(protected, key=str))

            masked_columns = self._unique(masked_columns)
            decision = Decision.MASK if masked_columns else Decision.ALLOW
            reason = (
                "Sensitive values are masked for your role."
                if decision is Decision.MASK
                else "This read-only query is permitted for your role."
            )
            return FirewallResult(
                decision=decision,
                reason=reason,
                safe_sql=qualified.sql(dialect=self.dialect),
                tables_touched=tables_touched,
                columns_touched=columns_touched,
                masked_columns=masked_columns,
            )
        except (SqlglotError, _Unresolvable, KeyError, TypeError, ValueError):
            return self._deny("I can't safely verify that SQL query, so I won't run it.")
        except Exception:
            # Security boundary: never allow or propagate an unexpected failure.
            return self._deny("I can't safely verify that SQL query, so I won't run it.")

    def _explicit_base_tables(self, expression: exp.Expression) -> list[TableRef]:
        """Resolve syntactic table references, excluding CTE names and subqueries."""
        cte_names = {
            cte.alias_or_name.lower()
            for cte in expression.find_all(exp.CTE)
            if cte.alias_or_name
        }
        tables: list[TableRef] = []
        for table in expression.find_all(exp.Table):
            name = table.name.lower()
            schema = table.db.lower()
            if not schema and name in cte_names:
                continue
            if not schema:
                raise _Unresolvable("unqualified table name")
            tables.append(TableRef(schema, name))
        return self._unique(tables)

    def _concrete_tables(self, expression: exp.Expression) -> list[TableRef]:
        tables: list[TableRef] = []
        for table in expression.find_all(exp.Table):
            schema, name = table.db.lower(), table.name.lower()
            if schema and name in self._catalog.get(schema, {}):
                tables.append(TableRef(schema, name))
        return tables

    def _first_denied_table(self, user: str, tables: Iterable[TableRef]):
        for table in tables:
            verdict = self.policy.check_table(user, table)
            if verdict.decision is Decision.DENY:
                return verdict
        return None

    def _resolve_column(self, scope: Scope, column: exp.Column, seen: set[tuple[int, str]]) -> set[ColumnRef]:
        source_name = column.table.lower()
        if not source_name or source_name not in scope.sources:
            raise _Unresolvable("column source is absent or ambiguous")
        source = scope.sources[source_name]
        if isinstance(source, exp.Table):
            schema, table, name = source.db.lower(), source.name.lower(), column.name.lower()
            if not schema or table not in self._catalog.get(schema, {}) or name not in self._catalog[schema][table]:
                raise _Unresolvable("column is not in the concrete catalog")
            return {ColumnRef(schema, table, name)}
        if not isinstance(source, Scope):
            raise _Unresolvable("unsupported column source")

        key = (id(source), column.name.lower())
        if key in seen:
            raise _Unresolvable("cyclic derived-column lineage")
        seen = seen | {key}
        matching = [
            select
            for select in source.expression.selects
            if select.alias_or_name and select.alias_or_name.lower() == column.name.lower()
        ]
        if len(matching) != 1:
            raise _Unresolvable("derived column is absent or ambiguous")
        origins = self._expression_origins(matching[0].this, source, seen)
        if not origins:
            raise _Unresolvable("derived column has no concrete lineage")
        return origins

    def _expression_origins(self, expression: exp.Expression, scope: Scope, seen: set[tuple[int, str]]) -> set[ColumnRef]:
        origins: set[ColumnRef] = set()
        for node in expression.walk(prune=lambda n: n is not expression and isinstance(n, exp.Query)):
            if isinstance(node, exp.Column):
                origins |= self._resolve_column(scope, node, seen)
        return origins

    def _in_inference_clause(self, column: exp.Column, scope: Scope) -> bool:
        node: exp.Expression | None = column
        # Do not stop at a nested SELECT: a scalar/EXISTS/IN subquery can itself
        # sit inside a parent WHERE or JOIN predicate, where selecting a protected
        # value becomes an inference channel.
        while node is not None:
            if isinstance(node, self._INFERENCE_CLAUSES):
                return True
            node = node.parent
        return False

    def _output_selects(self, query: exp.Query) -> list[exp.Select]:
        if isinstance(query, exp.Select):
            return [query]
        if isinstance(query, exp.SetOperation):
            return self._output_selects(query.this) + self._output_selects(query.expression)
        raise _Unresolvable("unsupported query output shape")

    def _order_uses_protected_output_alias(
        self,
        scope: Scope,
        protected_columns: set[ColumnRef],
        columns_by_node: dict[int, set[ColumnRef]],
        scope_by_expression: dict[int, Scope],
    ) -> set[ColumnRef]:
        """ORDER BY aliases are not retained as source Columns by SQLGlot.

        For example, qualification turns ``SELECT phone ... ORDER BY phone``
        into ``SELECT riders.phone AS phone ... ORDER BY phone``.  Resolve the
        output alias (and positional ORDER BY) back to its projection before
        deciding whether it creates an inference channel.
        """
        expression = scope.expression
        if not isinstance(expression, exp.Select) or not expression.args.get("order"):
            return set()
        projections = list(expression.expressions)
        aliases = {
            projection.alias_or_name.lower(): projection
            for projection in projections
            if projection.alias_or_name
        }
        for ordered in expression.args["order"].expressions:
            target = ordered.this
            projection = None
            if isinstance(target, exp.Column) and not target.table:
                projection = aliases.get(target.name.lower())
            elif isinstance(target, exp.Literal) and target.is_int:
                position = int(target.this)
                if 1 <= position <= len(projections):
                    projection = projections[position - 1]
            if projection is not None:
                origins = self._projection_origins(
                    projection, scope, columns_by_node, scope_by_expression
                )
                if origins & protected_columns:
                    return origins & protected_columns
        return set()

    def _projection_origins(
        self,
        projection: exp.Expression,
        scope: Scope,
        columns_by_node: dict[int, set[ColumnRef]],
        scope_by_expression: dict[int, Scope],
    ) -> set[ColumnRef]:
        origins: set[ColumnRef] = set()
        for node in projection.walk(prune=lambda n: n is not projection and isinstance(n, exp.Query)):
            if isinstance(node, exp.Column):
                origins |= columns_by_node.get(id(node), self._resolve_column(scope, node, set()))
            elif isinstance(node, exp.Subquery) and isinstance(node.this, exp.Query):
                child_scope = scope_by_expression.get(id(node.this))
                if child_scope is None:
                    raise _Unresolvable("scalar subquery could not be resolved")
                for child_projection in child_scope.expression.selects:
                    origins |= self._projection_origins(
                        child_projection, child_scope, columns_by_node, scope_by_expression
                    )
        return origins

    def _projection_is_aggregate_only(
        self,
        projection: exp.Expression,
        scope: Scope,
        scope_by_expression: dict[int, Scope],
    ) -> bool:
        # Every path from a projected PII column to this output must cross one of the
        # permitted aggregate nodes. Any unaggregated path means the value could leak.
        found_column = False
        for node in projection.walk(prune=lambda n: n is not projection and isinstance(n, exp.Query)):
            if isinstance(node, exp.Column):
                found_column = True
                parent: exp.Expression | None = node.parent
                aggregate_found = False
                while parent is not None and parent is not scope.expression:
                    if isinstance(parent, self._AGGREGATES):
                        aggregate_found = True
                        break
                    parent = parent.parent
                if not aggregate_found:
                    return False
            elif isinstance(node, exp.Subquery) and isinstance(node.this, exp.Query):
                child_scope = scope_by_expression.get(id(node.this))
                if child_scope is None:
                    raise _Unresolvable("scalar subquery could not be resolved")
                if not all(
                    self._projection_is_aggregate_only(child, child_scope, scope_by_expression)
                    for child in child_scope.expression.selects
                ):
                    return False
        return found_column

    def _mask_projection(self, projection: exp.Expression) -> None:
        masking_expression = sqlglot.parse_one(self.policy.masking_expr(ColumnRef("", "", "")), read=self.dialect)
        if isinstance(projection, exp.Alias):
            projection.set("this", masking_expression)
        else:
            projection.replace(masking_expression)

    _DISPLAY_NAMES = {
        "phone": "Phone numbers",
        "email": "Email addresses",
        "pan": "PAN numbers",
        "full_name": "Full names",
        "salary": "Salaries",
    }

    @classmethod
    def _masked_reason(cls, columns: Iterable[ColumnRef]) -> str:
        """Name the columns that actually triggered the refusal.

        The wording must follow the offending column, not a hard-coded example —
        telling someone who filtered on PAN that "phone numbers are masked" is both
        wrong and makes the refusal look canned.
        """
        names = sorted({cls._DISPLAY_NAMES.get(c.column, c.column.replace("_", " ").capitalize()) for c in columns})
        subject = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return (
            f"{subject} are masked for your role — I can count or average them, "
            "but not filter, sort, or group by them."
        )

    @staticmethod
    def _unique(items: Iterable[TableRef | ColumnRef]) -> list:
        result = []
        seen = set()
        for item in items:
            if item not in seen:
                result.append(item)
                seen.add(item)
        return result

    @staticmethod
    def _deny(reason: str) -> FirewallResult:
        return FirewallResult(Decision.DENY, reason, None)
