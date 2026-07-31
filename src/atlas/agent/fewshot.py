"""Mine few-shot examples from the audit log.

For each user, look back at recent successful ALLOW/MASK decisions and expose
them as ``(question, safe_sql)`` pairs. The context builder can prepend a
handful to LLM prompts to steer future generations toward known-good SQL
shapes.

Deliberately per-user: examples generated for gokul shouldn't leak
column/table names to arjun who can't see them.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.audit.audit import AuditLog


@dataclass(frozen=True)
class FewShotExample:
    question: str
    sql: str


class FewShotLibrary:
    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit

    def for_user(self, user: str, max_examples: int = 3) -> list[FewShotExample]:
        entries = self._audit.for_user(user)
        results: list[FewShotExample] = []
        seen: set[str] = set()
        for entry in entries:
            if entry.decision not in ("allow", "mask"):
                continue
            if not entry.executed_sql:
                continue
            q = entry.question.strip()
            if q in seen:
                continue
            seen.add(q)
            results.append(FewShotExample(question=q, sql=entry.executed_sql))
            if len(results) >= max_examples:
                break
        return results
