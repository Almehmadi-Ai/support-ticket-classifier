from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.database import create_all, create_engine, create_session_factory
from app.llm import FakeLLMClient, LLMClient
from app.models import Ticket
from app.schemas import (
    Category,
    Priority,
    TicketCreate,
    TicketList,
    TicketResponse,
    TicketStatus,
)
from app.worker import Worker


def create_app(
    *,
    llm: LLMClient | None = None,
    database_url: str | None = None,
    start_worker: bool = True,
) -> FastAPI:
    resolved_url = database_url or config.DATABASE_URL

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_engine(resolved_url)
        await create_all(engine)
        session_factory = create_session_factory(engine)
        llm_client = llm or FakeLLMClient()
        worker = Worker(session_factory, llm_client)

        app.state.session_factory = session_factory
        app.state.llm = llm_client
        app.state.worker = worker

        if start_worker:
            worker.start()
        try:
            yield
        finally:
            if start_worker:
                await worker.stop()
            await engine.dispose()

    app = FastAPI(title="Support Ticket Classifier", lifespan=lifespan)
    _register_routes(app)
    return app


async def _get_session(request: Request) -> AsyncSession:
    async with request.app.state.session_factory() as session:
        yield session


def _register_routes(app: FastAPI) -> None:
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/tickets", response_model=TicketResponse, status_code=201)
    async def create_ticket(
        payload: TicketCreate,
        request: Request,
        response: Response,
        session: AsyncSession = Depends(_get_session),
    ) -> TicketResponse:
        ticket = Ticket(
            id=payload.id,
            subject=payload.subject,
            body=payload.body,
            status=TicketStatus.pending.value,
        )
        session.add(ticket)
        try:
            await session.commit()
        except IntegrityError:
            # The primary key, not a pre-check, is what guarantees idempotency.
            await session.rollback()
            existing = await session.get(Ticket, payload.id)
            response.status_code = 200
            return TicketResponse.model_validate(existing)

        request.app.state.worker.notify()
        return TicketResponse.model_validate(ticket)

    @app.get("/tickets", response_model=TicketList)
    async def list_tickets(
        session: AsyncSession = Depends(_get_session),
        category: Category | None = None,
        priority: Priority | None = None,
        limit: int = Query(default=config.DEFAULT_PAGE_LIMIT, ge=1, le=config.MAX_PAGE_LIMIT),
        offset: int = Query(default=0, ge=0),
    ) -> TicketList:
        filters = []
        if category is not None:
            filters.append(Ticket.category == category.value)
        if priority is not None:
            filters.append(Ticket.priority == priority.value)

        count_stmt = select(func.count()).select_from(Ticket)
        page_stmt = select(Ticket).order_by(Ticket.created_at.desc(), Ticket.id.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            page_stmt = page_stmt.where(*filters)

        total = await session.scalar(count_stmt) or 0
        result = await session.execute(page_stmt.limit(limit).offset(offset))
        tickets = result.scalars().all()
        return TicketList(
            items=[TicketResponse.model_validate(t) for t in tickets],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/tickets/{ticket_id}", response_model=TicketResponse)
    async def get_ticket(
        ticket_id: str,
        session: AsyncSession = Depends(_get_session),
    ) -> TicketResponse:
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        return TicketResponse.model_validate(ticket)


app = create_app()
