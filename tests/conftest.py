from __future__ import annotations

import httpx
import pytest_asyncio
from httpx import ASGITransport

from app.database import create_all, create_engine, create_session_factory
from app.main import create_app  # registers the Ticket model via app.models


def _db_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path.as_posix()}/test.db"


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_engine(_db_url(tmp_path))
    await create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(tmp_path):
    # start_worker=False so tests drive worker.process_pending_once() deterministically.
    app = create_app(database_url=_db_url(tmp_path), start_worker=False)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client, app
