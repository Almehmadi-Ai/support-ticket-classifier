from __future__ import annotations

import pytest

from app.models import Ticket

TICKET = {
    "id": "t-1",
    "subject": "Charged twice",
    "body": "I was billed twice for one subscription, please refund one charge.",
}


async def test_create_ticket_returns_201_and_pending(client):
    http, _ = client
    response = await http.post("/tickets", json=TICKET)
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "t-1"
    assert data["status"] == "pending"
    assert data["category"] is None
    assert data["classification_attempts"] == 0


async def test_duplicate_id_returns_existing_without_creating_another(client):
    http, _ = client
    first = await http.post("/tickets", json=TICKET)
    assert first.status_code == 201
    second = await http.post("/tickets", json=TICKET)
    assert second.status_code == 200
    assert second.json()["id"] == "t-1"

    listing = await http.get("/tickets")
    assert listing.json()["total"] == 1


async def test_duplicate_id_does_not_reclassify(client):
    http, app = client
    await http.post("/tickets", json=TICKET)
    await http.post("/tickets", json=TICKET)

    processed = await app.state.worker.process_pending_once()
    assert processed == 1
    assert app.state.llm.calls == 1


async def test_duplicate_id_with_different_content_does_not_change_original(client):
    http, app = client
    await http.post("/tickets", json=TICKET)

    changed = {"id": "t-1", "subject": "different subject", "body": "different body"}
    response = await http.post("/tickets", json=changed)
    assert response.status_code == 200
    assert response.json()["subject"] == TICKET["subject"]
    assert response.json()["body"] == TICKET["body"]

    # only the original ticket exists and it is classified once
    processed = await app.state.worker.process_pending_once()
    assert processed == 1
    assert app.state.llm.calls == 1

    stored = (await http.get("/tickets/t-1")).json()
    assert stored["subject"] == TICKET["subject"]
    assert stored["body"] == TICKET["body"]


async def test_ticket_becomes_classified(client):
    http, app = client
    await http.post("/tickets", json=TICKET)
    await app.state.worker.process_pending_once()

    data = (await http.get("/tickets/t-1")).json()
    assert data["status"] == "classified"
    assert data["category"] in {"billing", "technical", "account", "other"}
    assert data["priority"] in {"low", "medium", "high"}
    assert data["summary"]
    assert data["classification_attempts"] == 1


async def test_get_ticket_by_id(client):
    http, _ = client
    await http.post("/tickets", json=TICKET)
    response = await http.get("/tickets/t-1")
    assert response.status_code == 200
    assert response.json()["subject"] == "Charged twice"


async def test_unknown_ticket_returns_404(client):
    http, _ = client
    response = await http.get("/tickets/does-not-exist")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"subject": "no id", "body": "x"},  # missing id
        {"id": "", "subject": "empty id", "body": "x"},  # id fails min_length
    ],
)
async def test_invalid_body_returns_422(client, payload):
    http, _ = client
    response = await http.post("/tickets", json=payload)
    assert response.status_code == 422


async def test_invalid_query_enum_returns_422(client):
    http, _ = client
    response = await http.get("/tickets", params={"category": "nonsense"})
    assert response.status_code == 422


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1000}, {"offset": -1}])
async def test_invalid_pagination_returns_422(client, params):
    http, _ = client
    assert (await http.get("/tickets", params=params)).status_code == 422


async def _seed_classified(app):
    rows = [
        ("k-1", "billing", "high"),
        ("k-2", "billing", "low"),
        ("k-3", "technical", "high"),
        ("k-4", "account", "medium"),
        ("k-5", "other", "low"),
    ]
    async with app.state.session_factory() as session:
        for ticket_id, category, priority in rows:
            session.add(
                Ticket(
                    id=ticket_id,
                    subject="s",
                    body="b",
                    status="classified",
                    category=category,
                    priority=priority,
                )
            )
        await session.commit()


async def test_filter_by_category(client):
    http, app = client
    await _seed_classified(app)
    data = (await http.get("/tickets", params={"category": "billing"})).json()
    assert data["total"] == 2
    assert all(item["category"] == "billing" for item in data["items"])


async def test_filter_by_priority(client):
    http, app = client
    await _seed_classified(app)
    data = (await http.get("/tickets", params={"priority": "high"})).json()
    assert data["total"] == 2
    assert all(item["priority"] == "high" for item in data["items"])


async def test_filter_by_category_and_priority(client):
    http, app = client
    await _seed_classified(app)
    data = (await http.get("/tickets", params={"category": "billing", "priority": "high"})).json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == "k-1"


async def test_pagination_splits_results(client):
    http, app = client
    await _seed_classified(app)

    page1 = (await http.get("/tickets", params={"limit": 2, "offset": 0})).json()
    page2 = (await http.get("/tickets", params={"limit": 2, "offset": 2})).json()
    page3 = (await http.get("/tickets", params={"limit": 2, "offset": 4})).json()

    assert page1["total"] == 5
    assert [len(p["items"]) for p in (page1, page2, page3)] == [2, 2, 1]

    ids = [item["id"] for page in (page1, page2, page3) for item in page["items"]]
    assert len(set(ids)) == 5  # pages are disjoint and cover everything
