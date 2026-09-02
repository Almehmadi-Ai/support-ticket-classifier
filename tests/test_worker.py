from __future__ import annotations

import asyncio

from app.llm import FakeLLMClient, LLMError
from app.models import Ticket
from app.schemas import TicketStatus
from app.worker import Worker

VALID = '{"category": "billing", "priority": "high", "summary": "Charged twice."}'


async def _add_pending(session_factory, ticket_id="t-1", subject="Charged twice", body="refund please"):
    async with session_factory() as session:
        session.add(
            Ticket(id=ticket_id, subject=subject, body=body, status=TicketStatus.pending.value)
        )
        await session.commit()


async def _get(session_factory, ticket_id="t-1") -> Ticket | None:
    async with session_factory() as session:
        return await session.get(Ticket, ticket_id)


async def test_successful_classification_persists_fields(session_factory):
    await _add_pending(session_factory)
    worker = Worker(session_factory, FakeLLMClient(responses=[VALID]), retry_backoff=0)

    assert await worker.process_pending_once() == 1

    ticket = await _get(session_factory)
    assert ticket.status == "classified"
    assert ticket.category == "billing"
    assert ticket.priority == "high"
    assert ticket.summary == "Charged twice."
    assert ticket.classification_attempts == 1
    assert ticket.last_error is None


async def test_model_exception_is_retried_then_succeeds(session_factory):
    await _add_pending(session_factory)
    llm = FakeLLMClient(responses=[LLMError("down"), "not json", VALID])
    worker = Worker(session_factory, llm, retry_backoff=0)

    await worker.process_pending_once()

    ticket = await _get(session_factory)
    assert ticket.status == "classified"
    assert ticket.classification_attempts == 3
    assert llm.calls == 3


async def test_exhausted_retries_mark_failed(session_factory):
    await _add_pending(session_factory)
    llm = FakeLLMClient(responses=[LLMError("provider down")])  # repeats every attempt
    worker = Worker(session_factory, llm, retry_backoff=0)

    await worker.process_pending_once()

    ticket = await _get(session_factory)
    assert ticket.status == "failed"
    assert ticket.classification_attempts == 3
    assert ticket.last_error
    assert ticket.category is None and ticket.priority is None and ticket.summary is None
    assert llm.calls == 3


async def test_malformed_output_never_stored_as_classification(session_factory):
    await _add_pending(session_factory)
    bad = '{"category": "urgent", "priority": "low", "summary": "x"}'
    worker = Worker(session_factory, FakeLLMClient(responses=[bad]), retry_backoff=0)

    await worker.process_pending_once()

    ticket = await _get(session_factory)
    assert ticket.status == "failed"
    assert ticket.category is None


async def test_pending_work_persisted_before_start_is_discovered(session_factory):
    # Simulates a ticket written by a previous run: the worker finds it by query.
    await _add_pending(session_factory, ticket_id="t-restart")
    worker = Worker(session_factory, FakeLLMClient(responses=[VALID]), retry_backoff=0)

    assert await worker.process_pending_once() == 1
    ticket = await _get(session_factory, "t-restart")
    assert ticket.status == "classified"


async def test_running_loop_processes_pending_after_notify(session_factory):
    worker = Worker(
        session_factory, FakeLLMClient(responses=[VALID]), retry_backoff=0, poll_interval=0.05
    )
    worker.start()
    try:
        await _add_pending(session_factory, ticket_id="t-loop")
        worker.notify()
        await _wait_for_status(session_factory, "t-loop", "classified")
    finally:
        await worker.stop()


async def _wait_for_status(session_factory, ticket_id, status, timeout=2.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        ticket = await _get(session_factory, ticket_id)
        if ticket is not None and ticket.status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{ticket_id} did not reach status {status!r} in time")
