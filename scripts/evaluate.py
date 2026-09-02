from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.classifier import ClassificationError, classify_ticket
from app.llm import FakeLLMClient

DATA = Path(__file__).resolve().parent / "labeled_tickets.json"


async def _evaluate() -> None:
    tickets = json.loads(DATA.read_text(encoding="utf-8"))
    llm = FakeLLMClient()

    category_hits = 0
    priority_hits = 0
    for ticket in tickets:
        try:
            result = await classify_ticket(ticket["subject"], ticket["body"], llm)
        except ClassificationError:
            continue
        category_hits += result.category.value == ticket["expected_category"]
        priority_hits += result.priority.value == ticket["expected_priority"]

    total = len(tickets)
    print(f"{total} tickets")
    print(f"{category_hits} matched expected category")
    print(f"{priority_hits} matched expected priority")
    print("(measures the bundled deterministic fake, not real-model quality)")


def main() -> None:
    asyncio.run(_evaluate())


if __name__ == "__main__":
    main()
