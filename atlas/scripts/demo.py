"""Run the five-question governed analyst demonstration offline by default."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.agent.analyst import Analyst  # noqa: E402


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
