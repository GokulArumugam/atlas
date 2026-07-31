"""The policy engine: says yes/no/mask, and nothing more.

It only decides *whether* someone can see something — it never parses or rewrites
SQL (that's the firewall's job). Everything here fails closed: unknown user,
unknown table, unknown column all get a "no."
"""

from __future__ import annotations

from .model import ColumnRef, Decision, TableRef, Verdict, display_name
from .policies import POLICY_CONFIG


class PolicyEngine:
    def __init__(self, config: dict | None = None) -> None:
        self.config = POLICY_CONFIG if config is None else config

    def users(self) -> list[str]:
        return list(self.config.get("users", {}).keys())

    def team_of(self, user: str) -> str:
        user_config = self._user_config(user)
        return user_config.get("team", "unknown") if user_config else "unknown"

    def visible_tables(self, user: str) -> set[TableRef]:
        user_config = self._user_config(user)
        if not user_config:
            return set()
        return {TableRef(schema, table) for schema, table in user_config.get("visible_tables", set())}

    def check_table(self, user: str, table: TableRef) -> Verdict:
        user_config = self._user_config(user)
        if not user_config:
            return self._deny_table(table, "I can't access data for an unrecognized user.")
        # Careful here: the refusal message must NOT depend on whether the table
        # actually exists. If we said "that table doesn't exist" vs "you can't see
        # that table," an attacker could probe names and learn what's real just from
        # the wording. So anything outside the user's visible set gets the exact same
        # answer — "exists but forbidden" and "doesn't exist" look identical.
        if table not in self.visible_tables(user):
            return self._deny_table(table, self._table_denial_reason(user, table))
        return Verdict(Decision.ALLOW, "Access to this table is permitted for your role.", [], [], [])

    def check_column(self, user: str, column: ColumnRef) -> Verdict:
        table = TableRef(column.schema, column.table)
        table_verdict = self.check_table(user, table)
        if table_verdict.decision is Decision.DENY:
            return Verdict(Decision.DENY, table_verdict.reason, [], [table], [column])
        if not self._known_column(column):
            return Verdict(
                Decision.DENY,
                "I can't access that column because it is not in the approved data catalog.",
                [], [], [column],
            )
        if self.is_pii(column) and self._column_key(column) not in self._user_config(user).get("unmasked_pii", set()):
            return Verdict(
                Decision.MASK,
                f"{self._display_name(column.column)} are masked for your role. I can still count or group by them.",
                [column], [], [],
            )
        return Verdict(Decision.ALLOW, "Access to this column is permitted for your role.", [], [], [])

    def masking_expr(self, column: ColumnRef) -> str:
        # The caller may only substitute this after receiving a MASK verdict.
        # Per-column overrides let policies emit type-safe masks (e.g. NULL for
        # numerics so UNION with unmasked numeric columns still typechecks).
        overrides = self.config.get("column_masking_expressions", {})
        key = self._column_key(column)
        if key in overrides:
            return overrides[key]
        column_types = self.config.get("column_types", {})
        col_type = column_types.get(key)
        if col_type is not None:
            if col_type in {"int", "bigint", "float", "double", "decimal", "numeric"}:
                return "NULL"
            if col_type == "date":
                return "CAST(NULL AS DATE)"
            if col_type == "timestamp":
                return "CAST(NULL AS TIMESTAMP)"
            if col_type == "bool":
                return "CAST(NULL AS BOOLEAN)"
        return self.config.get("masking_expression", "'***MASKED***'")

    def catalog_tables(self) -> dict:
        """The schema -> table -> columns map the firewall builds its view from.

        Exposing this (instead of the firewall importing the global config) means
        the firewall and this engine can never quietly disagree about what the
        catalog is — they read from the exact same injected config.
        """
        return self.config.get("tables", {})

    def is_pii(self, column: ColumnRef) -> bool:
        return self._column_key(column) in self.config.get("pii_columns", set())

    def _user_config(self, user: str) -> dict | None:
        return self.config.get("users", {}).get(user)

    def _known_column(self, column: ColumnRef) -> bool:
        return column.column in self.config.get("tables", {}).get(column.schema, {}).get(column.table, set())

    @staticmethod
    def _column_key(column: ColumnRef) -> tuple[str, str, str]:
        return (column.schema, column.table, column.column)

    @staticmethod
    def _display_name(name: str) -> str:
        return display_name(name)

    def _table_denial_reason(self, user: str, table: TableRef) -> str:
        team = self.team_of(user)
        if team == "engineering" and table.schema == "hr":
            return "You're on the engineering team — I can't access HR data."
        if team == "audit":
            return "You're on the audit team — the audit console does not expose rides or HR data."
        return f"You're on the {team} team — I can't access this data for your role."

    @staticmethod
    def _deny_table(table: TableRef, reason: str) -> Verdict:
        return Verdict(Decision.DENY, reason, [], [table], [])
