"""Opt-in PostgreSQL verification; never runs against the operational database."""

import asyncio
import os
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contracts import EntryOpportunityEvent, EventEnvelope
from app.integration.entry_opportunity_commands import EntryOpportunityCommandProcessor
from app.integration.entry_opportunity_store import PostgresEntryOpportunityStore
from app.integration.tests.test_entry_opportunity_store import NOW, opportunity
from app.persistence import PersistenceUnitOfWork, create_database_engine, create_session_factory
from app.persistence.models import Base, EntryOpportunityCommandRecord, OutboxEvent

pytestmark = pytest.mark.integration


@pytest.fixture
async def database() -> AsyncGenerator[AsyncEngine]:
    url = os.environ.get("TEST_QUEUE_DATABASE_URL")
    if not url:
        pytest.skip("TEST_QUEUE_DATABASE_URL is not set")
    name = make_url(url).database or ""
    if not name.startswith("marketbot_queue_test_"):
        raise ValueError(
            "queue integration tests require an isolated marketbot_queue_test_ database"
        )
    engine = create_database_engine(url, require_ssl=False)
    async with engine.begin() as connection:
        await connection.execute(text("create schema if not exists market_bot"))
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.parametrize("failure", ["exception", "disconnect"])
async def test_rollback_atomicity_fifo_and_two_concurrent_writers(
    database: AsyncEngine, failure: str
) -> None:
    factory = create_session_factory(database)
    store = PostgresEntryOpportunityStore(factory)
    await store.save(opportunity(), None)
    first = EventEnvelope(event_type="test.update", source="test", occurred_at=NOW, payload={})
    second = EventEnvelope(
        event_type="test.update", source="test", occurred_at=NOW - timedelta(days=1), payload={}
    )
    for envelope in (first, second):
        async with PersistenceUnitOfWork(factory) as unit:
            await unit.entry_opportunity_commands.enqueue(
                source_event_id=envelope.event_id,
                source_subject="test",
                command_type="test",
                symbol="AAPL",
                occurred_at=envelope.occurred_at,
                payload=envelope.model_dump(mode="json"),
            )
    async with factory() as session, session.begin():
        await session.execute(update(EntryOpportunityCommandRecord).values(available_at=NOW))
    fail = True
    received: list[EventEnvelope] = []

    async def apply(_: str, envelope: EventEnvelope) -> None:
        active = await store.load_active("AAPL")
        assert active is not None
        changed = active.model_copy(update={"revision": active.revision + 1})
        event = EntryOpportunityEvent(
            event_id=envelope.event_id,
            opportunity=changed,
            occurred_at=NOW,
            reasons=("test",),
        )
        await store.save(changed, event)
        assert (await store.load_active("AAPL")).revision == changed.revision
        if fail:
            if failure == "disconnect":
                unit = store._bound_unit.get()
                assert unit is not None
                await unit._require_session().execute(
                    text("select pg_terminate_backend(pg_backend_pid())")
                )
            raise RuntimeError("injected failure after aggregate and outbox writes")
        received.append(envelope)
        await asyncio.sleep(0)

    writer = EntryOpportunityCommandProcessor(factory, store=store, apply=apply, clock=lambda: NOW)
    with pytest.raises((RuntimeError, DBAPIError)):
        await writer.drain_once()
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(EntryOpportunityCommandRecord)
                .where(EntryOpportunityCommandRecord.status == "PROCESSING")
            )
            == 0
        )
    fail = False
    another = EntryOpportunityCommandProcessor(factory, store=store, apply=apply, clock=lambda: NOW)
    assert await asyncio.gather(writer.drain_once(), another.drain_once()) == [True, True]
    assert received == [first, second]
    assert await writer.drain_once() is False
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(OutboxEvent)) == 2
