from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import config
from app.classifier import ClassificationError, classify_ticket
from app.llm import LLMClient
from app.models import Ticket
from app.schemas import ClassificationResult, TicketStatus


class Worker:
    """In-process classification worker.

    The database is the source of truth: pending tickets are found by query, so work
    persisted before a restart is picked up again on the next start. The wake event is
    only an optimisation to avoid waiting for the poll interval.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        llm: LLMClient,
        *,
        max_concurrency: int = config.MAX_CONCURRENT_CLASSIFICATIONS,
        max_attempts: int = config.MAX_CLASSIFICATION_ATTEMPTS,
        retry_backoff: float = config.RETRY_BACKOFF_SECONDS,
        poll_interval: float = config.WORKER_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_attempts = max_attempts
        self._retry_backoff = retry_backoff
        self._poll_interval = poll_interval
        self._wake = asyncio.Event()
        self._in_flight: set[str] = set()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def notify(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            await self.process_pending_once()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass
            finally:
                self._wake.clear()

    async def process_pending_once(self) -> int:
        """Process every currently pending ticket to completion. Returns the count.

        Tests call this directly to wait for processing without relying on timing.
        """
        ticket_ids = await self._claim_pending()
        if not ticket_ids:
            return 0
        await asyncio.gather(*(self._process(ticket_id) for ticket_id in ticket_ids))
        return len(ticket_ids)

    async def _claim_pending(self) -> list[str]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Ticket.id)
                .where(Ticket.status == TicketStatus.pending.value)
                .order_by(Ticket.created_at)
            )
            candidates = list(result.scalars())
        # Skip tickets already being handled in this process so an overlapping scan
        # cannot dispatch the same one twice. This guard is in-process only.
        fresh = [ticket_id for ticket_id in candidates if ticket_id not in self._in_flight]
        self._in_flight.update(fresh)
        return fresh

    async def _process(self, ticket_id: str) -> None:
        async with self._semaphore:
            try:
                await self._classify_and_store(ticket_id)
            finally:
                self._in_flight.discard(ticket_id)

    async def _classify_and_store(self, ticket_id: str) -> None:
        content = await self._load_pending_content(ticket_id)
        if content is None:
            return
        subject, body = content

        last_error: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                result = await classify_ticket(subject, body, self._llm)
            except ClassificationError as exc:
                last_error = str(exc)
                if attempt < self._max_attempts and self._retry_backoff > 0:
                    await asyncio.sleep(self._retry_backoff)
                continue
            await self._store_success(ticket_id, result, attempt)
            return

        await self._store_failure(ticket_id, last_error, self._max_attempts)

    async def _load_pending_content(self, ticket_id: str) -> tuple[str, str] | None:
        # Loaded and released before classifying so we never hold a transaction open
        # across the (potentially slow) model call.
        async with self._session_factory() as session:
            ticket = await session.get(Ticket, ticket_id)
            if ticket is None or ticket.status != TicketStatus.pending.value:
                return None
            return ticket.subject, ticket.body

    async def _store_success(
        self, ticket_id: str, result: ClassificationResult, attempts: int
    ) -> None:
        async with self._session_factory() as session:
            ticket = await session.get(Ticket, ticket_id)
            if ticket is None or ticket.status != TicketStatus.pending.value:
                return
            ticket.status = TicketStatus.classified.value
            ticket.category = result.category.value
            ticket.priority = result.priority.value
            ticket.summary = result.summary
            ticket.classification_attempts = attempts
            ticket.last_error = None
            await session.commit()

    async def _store_failure(
        self, ticket_id: str, last_error: str | None, attempts: int
    ) -> None:
        async with self._session_factory() as session:
            ticket = await session.get(Ticket, ticket_id)
            if ticket is None or ticket.status != TicketStatus.pending.value:
                return
            ticket.status = TicketStatus.failed.value
            ticket.classification_attempts = attempts
            ticket.last_error = (last_error or "classification failed")[
                : config.MAX_STORED_ERROR_LENGTH
            ]
            await session.commit()
