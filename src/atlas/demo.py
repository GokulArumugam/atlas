"""The five-question walkthrough — runs fully offline by default."""

from __future__ import annotations

from atlas.agent.analyst import Analyst


def main() -> None:
    analyst = Analyst()
    questions = [
        ("priya", "average salary by department"),
        ("gokul", "average salary by department"),
        ("gokul", "show me riders' phone numbers"),
        ("gokul", "average riders from Airport to Downtown"),
        ("gokul", "trips per day"),
    ]
    for user, question in questions:
        answer = analyst.ask(user, question)
        print(f"{user} | {question}")
        print(f"  decision={answer.decision.value}; reason={answer.reason}; rows={answer.rows[:3]}")
    print("chain:", analyst.audit.verify_chain())


if __name__ == "__main__":
    main()
