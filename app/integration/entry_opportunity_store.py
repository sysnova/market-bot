"""PostgreSQL adapter for the consolidated Entry Watcher opportunity lifecycle."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import (
    ENTRY_OPPORTUNITY_EVENT,
    EntryOpportunity,
    EntryOpportunityEvent,
    EventEnvelope,
    entry_opportunity_subject,
)
from app.persistence import (
    EntryOpportunityEventRecord,
    EntryOpportunityRecord,
    OutboxRepository,
    PersistenceUnitOfWork,
)


class PostgresEntryOpportunityStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        source: str = "entry-opportunity",
    ) -> None:
        self._session_factory = session_factory
        self._source = source

    async def is_ready(self) -> bool:
        async with self._session_factory() as session:
            opportunity = await session.scalar(
                text("select to_regclass('market_bot.entry_opportunities')")
            )
            event = await session.scalar(
                text("select to_regclass('market_bot.entry_opportunity_events')")
            )
            outbox = await session.scalar(text("select to_regclass('market_bot.outbox_events')"))
            return opportunity is not None and event is not None and outbox is not None

    async def load_active(self, symbol: str) -> EntryOpportunity | None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            record = await unit.entry_opportunities.load_active(symbol)
        return _to_domain(record) if record is not None else None

    async def load_latest(self, symbol: str) -> EntryOpportunity | None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            record = await unit.entry_opportunities.load_latest(symbol)
        return _to_domain(record) if record is not None else None

    async def list_active(self) -> tuple[EntryOpportunity, ...]:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            records = await unit.entry_opportunities.list_active()
        return tuple(_to_domain(record) for record in records)

    async def list_recent(self, *, limit: int) -> tuple[EntryOpportunity, ...]:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            records = await unit.entry_opportunities.list_recent(limit=limit)
        return tuple(_to_domain(record) for record in records)

    async def event_seen(self, event_id: UUID) -> bool:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            return await unit.entry_opportunities.event_seen(event_id)

    async def latest_events(
        self,
        opportunity_ids: tuple[UUID, ...],
    ) -> tuple[EntryOpportunityEvent, ...]:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            records = await unit.entry_opportunities.latest_events(opportunity_ids)
        return tuple(
            EntryOpportunityEvent.model_validate(record.payload, strict=False)
            for record in records
        )

    async def save(
        self,
        opportunity: EntryOpportunity,
        event: EntryOpportunityEvent | None,
    ) -> None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            changed = await unit.entry_opportunities.save(
                _to_record(opportunity),
                _event_record(event) if event is not None else None,
            )
            if not changed:
                raise RuntimeError("entry opportunity update lost an optimistic race")
            if event is not None:
                await self._enqueue(unit.outbox, event)

    async def _enqueue(
        self,
        outbox: OutboxRepository,
        event: EntryOpportunityEvent,
    ) -> None:
        envelope = EventEnvelope(
            event_type=ENTRY_OPPORTUNITY_EVENT,
            occurred_at=event.occurred_at,
            source=self._source,
            subject=event.opportunity.symbol,
            payload=event,
            causation_id=event.event_id,
        )
        await outbox.enqueue(
            aggregate_type="entry-opportunity",
            aggregate_id=str(event.opportunity.opportunity_id),
            event_type=envelope.event_type,
            subject=entry_opportunity_subject(
                event.opportunity.status,
                event.opportunity.symbol,
            ),
            payload=envelope.model_dump(mode="json"),
            occurred_at=event.occurred_at,
        )


def _to_record(opportunity: EntryOpportunity) -> EntryOpportunityRecord:
    return EntryOpportunityRecord(
        id=opportunity.opportunity_id,
        symbol=opportunity.symbol,
        status=opportunity.status.value,
        current_maturity=opportunity.current_maturity.value,
        peak_maturity=opportunity.peak_maturity.value,
        progress_percent=opportunity.progress_percent,
        original_watch_id=opportunity.original_watch_id,
        armed_at=opportunity.armed_at,
        updated_at=opportunity.updated_at,
        expires_at=opportunity.expires_at,
        closed_at=opportunity.closed_at,
        close_reason=(
            opportunity.close_reason.value if opportunity.close_reason is not None else None
        ),
        zone_low=opportunity.zone_low,
        zone_high=opportunity.zone_high,
        invalidation=opportunity.invalidation,
        original_price=opportunity.original_price,
        current_price=opportunity.current_price,
        revision=opportunity.revision,
        payload=opportunity.model_dump(mode="json"),
        created_at=opportunity.armed_at,
    )


def _to_domain(record: EntryOpportunityRecord) -> EntryOpportunity:
    return EntryOpportunity.model_validate(record.payload, strict=False)


def _event_record(event: EntryOpportunityEvent) -> EntryOpportunityEventRecord:
    return EntryOpportunityEventRecord(
        id=event.event_id,
        opportunity_id=event.opportunity.opportunity_id,
        symbol=event.opportunity.symbol,
        occurred_at=event.occurred_at,
        reasons=list(event.reasons),
        payload=event.model_dump(mode="json"),
        created_at=event.occurred_at,
    )
