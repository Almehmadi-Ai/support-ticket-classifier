from __future__ import annotations

import json
from pathlib import Path

import httpx

SAMPLES = Path(__file__).resolve().parent.parent / "sample_tickets.json"
BASE_URL = "http://localhost:8000"


def main() -> None:
    tickets = json.loads(SAMPLES.read_text(encoding="utf-8"))
    with httpx.Client(base_url=BASE_URL) as client:
        for ticket in tickets:
            response = client.post("/tickets", json=ticket)
            print(f"{ticket['id']}: {response.status_code}")


if __name__ == "__main__":
    main()
