"""Focused repositories for idempotent delivery and operational state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ConsumerCheckpoint,
    OutboxEvent,
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
