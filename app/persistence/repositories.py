"""Focused repositories for idempotent delivery and operational state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ConsumerCheckpoint,
    EntryWatchRecord,
    EntryWatchTransitionRecord,
    LongPortfolioAlertRecord,
    LongPortfolioStateRecord,
    OutboxEvent,
    PatreonCapsTransitionRecord,
    PatreonCapsWatchRecord,
    ProcessedEvent,
    ServiceHealthRecord,
    new_entity_id,
    utc_now,
)

IdFactory = Callable[[], UUID]
Clock = Callable[[], datetime]


class EventPayloadConflictError(RuntimeError):
    """A redelivered event ID carried different immutable content."""

    def __init__(self, *, consumer_name: str, event_id: UUID) -> None:
        super().__init__(
            "processed event payload hash conflict: "
            f"consumer={consumer_name!r}, event_id={event_id}"
        )


class Repository:
    """Base repository with injected entropy and time."""

    dialect = postgresql.dialect()

    def __init__(
        self,
        session: AsyncSession,
        *,
        id_factory: IdFactory = new_entity_id,
        clock: Clock = utc_now,
    ) -> None:
        self._session = session
        self._id_factory = id_factory
        self._clock = clock


class ProcessedEventRepository(Repository):
    """Idempotency inbox keyed by consumer and source event."""

    async def record(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        subject: str,
        payload_hash: str,
        run_id: UUID | None = None,
    ) -> bool:
        statement = (
            insert(ProcessedEvent)
            .values(
                id=self._id_factory(),
                consumer_name=consumer_name,
                event_id=event_id,
                run_id=run_id,
                subject=subject,
                payload_hash=payload_hash,
                processed_at=self._clock(),
            )
            .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
            .returning(ProcessedEvent.id)
        )
        result = await self._session.execute(statement)
        if result.scalar_one_or_none() is not None:
            return True

        existing_hash_statement = select(ProcessedEvent.payload_hash).where(
            ProcessedEvent.consumer_name == consumer_name,
            ProcessedEvent.event_id == event_id,
        )
        existing_result = await self._session.execute(existing_hash_statement)
        existing_hash = existing_result.scalar_one_or_none()
        if existing_hash != payload_hash:
            raise EventPayloadConflictError(consumer_name=consumer_name, event_id=event_id)
        return False


InboxRepository = ProcessedEventRepository


class OutboxRepository(Repository):
    """Transactional outbox with non-blocking multi-worker claiming."""

    async def enqueue(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        subject: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        available_at: datetime | None = None,
    ) -> OutboxEvent:
        now = self._clock()
        event = OutboxEvent(
            id=self._id_factory(),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            subject=subject,
            payload=payload,
            headers=headers or {},
            occurred_at=occurred_at or now,
            available_at=available_at or now,
            created_at=now,
        )
        self._session.add(event)
        return event

    async def claim_pending(self, *, limit: int, now: datetime) -> list[OutboxEvent]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.published_at.is_(None),
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def mark_published(self, event_id: UUID, *, published_at: datetime | None = None) -> None:
        statement = (
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id, OutboxEvent.published_at.is_(None))
            .values(published_at=published_at or self._clock(), last_error=None)
        )
        await self._session.execute(statement)

    async def record_failure(self, event_id: UUID, *, error: str) -> None:
        statement = (
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id, OutboxEvent.published_at.is_(None))
            .values(attempts=OutboxEvent.attempts + 1, last_error=error)
        )
        await self._session.execute(statement)


class CheckpointRepository(Repository):
    """Monotonic consumer position storage."""

    async def advance(self, *, consumer_name: str, stream: str, sequence: int) -> None:
        if sequence < 0:
            raise ValueError("sequence must be nonnegative")
        now = self._clock()
        base_statement = insert(ConsumerCheckpoint).values(
            id=self._id_factory(),
            consumer_name=consumer_name,
            stream=stream,
            sequence=sequence,
            updated_at=now,
        )
        statement = base_statement.on_conflict_do_update(
            index_elements=["consumer_name", "stream"],
            set_={
                "sequence": func.greatest(
                    ConsumerCheckpoint.sequence, base_statement.excluded.sequence
                ),
                "updated_at": now,
            },
        )
        await self._session.execute(statement)


class HealthRepository(Repository):
    """Latest health snapshot for an engine process."""

    async def upsert(
        self,
        *,
        service_name: str,
        status: str,
        details: dict[str, Any],
        observed_at: datetime,
    ) -> None:
        now = self._clock()
        base_statement = insert(ServiceHealthRecord).values(
            id=self._id_factory(),
            service_name=service_name,
            status=status,
            details=details,
            observed_at=observed_at,
            updated_at=now,
        )
        statement = base_statement.on_conflict_do_update(
            index_elements=["service_name"],
            set_={
                "status": base_statement.excluded.status,
                "details": base_statement.excluded.details,
                "observed_at": base_statement.excluded.observed_at,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)


class EntryWatchRepository(Repository):
    """Persist one active entry thesis per symbol and its immutable transitions."""

    async def load_active(self, symbol: str) -> EntryWatchRecord | None:
        statement = (
            select(EntryWatchRecord)
            .where(
                EntryWatchRecord.symbol == symbol.strip().upper(),
                EntryWatchRecord.status.in_(("ARMED", "IN_ZONE")),
            )
            .order_by(EntryWatchRecord.armed_at.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)

    def add(
        self,
        watch: EntryWatchRecord,
        transition: EntryWatchTransitionRecord,
    ) -> None:
        self._session.add_all((watch, transition))

    async def apply_transition(
        self,
        watch_id: UUID,
        *,
        previous_status: str,
        status: str,
        current_price: Decimal,
        updated_at: datetime,
        terminal_at: datetime | None,
        transition: EntryWatchTransitionRecord,
    ) -> bool:
        statement = (
            update(EntryWatchRecord)
            .where(
                EntryWatchRecord.id == watch_id,
                EntryWatchRecord.status == previous_status,
            )
            .values(
                status=status,
                current_price=current_price,
                updated_at=updated_at,
                terminal_at=terminal_at,
            )
            .returning(EntryWatchRecord.id)
        )
        result = await self._session.execute(statement)
        if result.scalar_one_or_none() is None:
            return False
        self._session.add(transition)
        return True


class LongPortfolioAlertRepository(Repository):
    """Append an immutable LONG alert exactly once by its stable deduplication key."""

    async def add(self, record: LongPortfolioAlertRecord) -> bool:
        values = {
            column.name: getattr(record, column.name)
            for column in LongPortfolioAlertRecord.__table__.columns
        }
        statement = (
            insert(LongPortfolioAlertRecord)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["deduplication_key"])
            .returning(LongPortfolioAlertRecord.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None


class PatreonCapsRepository(Repository):
    """Atomically update one watch snapshot and append each transition once."""

    async def load_active(self) -> tuple[PatreonCapsWatchRecord, ...]:
        records = await self._session.scalars(
            select(PatreonCapsWatchRecord)
            .where(PatreonCapsWatchRecord.state.not_in(("INVALIDATED", "EXPIRED")))
            .order_by(PatreonCapsWatchRecord.symbol)
        )
        return tuple(records.all())

    async def recent_transitions(self, *, limit: int) -> tuple[PatreonCapsTransitionRecord, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        records = await self._session.scalars(
            select(PatreonCapsTransitionRecord)
            .order_by(PatreonCapsTransitionRecord.occurred_at.desc())
            .limit(limit)
        )
        return tuple(reversed(records.all()))

    async def save(
        self,
        watch: PatreonCapsWatchRecord,
        transition: PatreonCapsTransitionRecord,
    ) -> bool:
        watch_values = {
            column.name: getattr(watch, column.name)
            for column in PatreonCapsWatchRecord.__table__.columns
        }
        watch_insert = insert(PatreonCapsWatchRecord).values(**watch_values)
        await self._session.execute(
            watch_insert.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "state": watch_insert.excluded.state,
                    "updated_at": watch_insert.excluded.updated_at,
                    "highest_price": watch_insert.excluded.highest_price,
                    "tranche_stage": watch_insert.excluded.tranche_stage,
                    "saw_macro_shock": watch_insert.excluded.saw_macro_shock,
                    "source_analysis_ids": watch_insert.excluded.source_analysis_ids,
                    "payload": watch_insert.excluded.payload,
                },
            )
        )
        transition_values = {
            column.name: getattr(transition, column.name)
            for column in PatreonCapsTransitionRecord.__table__.columns
        }
        statement = (
            insert(PatreonCapsTransitionRecord)
            .values(**transition_values)
            .on_conflict_do_nothing(index_elements=["deduplication_key"])
            .returning(PatreonCapsTransitionRecord.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None


class LongPortfolioStateRepository(Repository):
    """Load and upsert bounded confirmation state by rule version and symbol."""

    async def load(self, *, rule_version: str) -> tuple[LongPortfolioStateRecord, ...]:
        records = await self._session.scalars(
            select(LongPortfolioStateRecord)
            .where(LongPortfolioStateRecord.rule_version == rule_version)
            .order_by(LongPortfolioStateRecord.symbol)
        )
        return tuple(records.all())

    async def upsert(self, record: LongPortfolioStateRecord) -> None:
        values = {
            column.name: getattr(record, column.name)
            for column in LongPortfolioStateRecord.__table__.columns
        }
        statement = insert(LongPortfolioStateRecord).values(**values)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=["rule_version", "symbol"],
                set_={
                    "qualified_sessions": statement.excluded.qualified_sessions,
                    "last_emitted": statement.excluded.last_emitted,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
