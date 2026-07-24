"""Fail-closed policy verdicts; SQL parsing and enforcement belong to a later firewall."""

from __future__ import annotations

from .model import ColumnRef, Decision, TableRef, Verdict
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
        # NOTE (security, README 5B): the denial reason must NOT depend on whether the
        # table actually exists. Branching on catalog existence here would turn every
        # refusal into an enumeration oracle — probe a name, and the wording tells you
        # whether it is real. Anything outside the user's visible set gets one identical
        # answer, so "exists but forbidden" and "does not exist" are indistinguishable.
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
        return self.config.get("masking_expression", "'***MASKED***'")

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
        if name == "phone":
            return "Phone numbers"
        if name == "email":
            return "Email addresses"
        if name == "pan":
            return "PAN values"
        if name == "full_name":
            return "Full names"
        return name.replace("_", " ").capitalize()

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
