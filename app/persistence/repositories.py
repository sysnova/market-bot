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
    AlertAnalysisStateRecord,
    AlertContinuationCandidateRecord,
    AlertContinuationSessionRecord,
    ConsumerCheckpoint,
    EngineDecisionStateRecord,
    EntryOpportunityEventRecord,
    EntryOpportunityRecord,
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

    async def claim_pending(
        self,
        *,
        limit: int,
        now: datetime,
        lease_until: datetime,
    ) -> list[OutboxEvent]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if lease_until <= now:
            raise ValueError("lease_until must be after now")
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
        events = list(result.all())
        for event in events:
            event.available_at = lease_until
            event.attempts = (event.attempts or 0) + 1
        return events

    async def mark_published(self, event_id: UUID, *, published_at: datetime | None = None) -> None:
        statement = (
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id, OutboxEvent.published_at.is_(None))
            .values(published_at=published_at or self._clock(), last_error=None)
        )
        await self._session.execute(statement)

    async def record_failure(
        self,
        event_id: UUID,
        *,
        error: str,
        available_at: datetime,
    ) -> None:
        statement = (
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id, OutboxEvent.published_at.is_(None))
            .values(last_error=error, available_at=available_at)
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


class EngineDecisionStateRepository(Repository):
    """Latest bounded checkpoint for a stateful decision engine."""

    async def load(
        self,
        engine_name: str,
        implementation_version: str,
    ) -> EngineDecisionStateRecord | None:
        statement = select(EngineDecisionStateRecord).where(
            EngineDecisionStateRecord.engine_name == engine_name.strip().lower(),
            EngineDecisionStateRecord.implementation_version == implementation_version,
        )
        return await self._session.scalar(statement)

    async def upsert(
        self,
        *,
        engine_name: str,
        implementation_version: str,
        state_schema_version: str,
        payload: dict[str, Any],
    ) -> None:
        now = self._clock()
        base_statement = insert(EngineDecisionStateRecord).values(
            id=self._id_factory(),
            engine_name=engine_name.strip().lower(),
            implementation_version=implementation_version,
            state_schema_version=state_schema_version,
            payload=payload,
            updated_at=now,
        )
        statement = base_statement.on_conflict_do_update(
            index_elements=["engine_name", "implementation_version"],
            set_={
                "state_schema_version": base_statement.excluded.state_schema_version,
                "payload": base_statement.excluded.payload,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)


class AlertDecisionStateRepository(Repository):
    """Normalized, incrementally updated Alert Engine recovery state."""

    async def load_analyses(
        self,
        engine_name: str,
        implementation_version: str,
    ) -> tuple[AlertAnalysisStateRecord, ...]:
        statement = select(AlertAnalysisStateRecord).where(
            AlertAnalysisStateRecord.engine_name == engine_name.strip().lower(),
            AlertAnalysisStateRecord.implementation_version == implementation_version,
        )
        return tuple((await self._session.scalars(statement)).all())

    async def load_candidates(
        self,
        engine_name: str,
        implementation_version: str,
    ) -> tuple[AlertContinuationCandidateRecord, ...]:
        statement = select(AlertContinuationCandidateRecord).where(
            AlertContinuationCandidateRecord.engine_name == engine_name.strip().lower(),
            AlertContinuationCandidateRecord.implementation_version == implementation_version,
            AlertContinuationCandidateRecord.active.is_(True),
        )
        return tuple((await self._session.scalars(statement)).all())

    async def load_sessions(
        self,
        engine_name: str,
        implementation_version: str,
    ) -> tuple[AlertContinuationSessionRecord, ...]:
        statement = select(AlertContinuationSessionRecord).where(
            AlertContinuationSessionRecord.engine_name == engine_name.strip().lower(),
            AlertContinuationSessionRecord.implementation_version == implementation_version,
        )
        return tuple((await self._session.scalars(statement)).all())

    async def upsert_analyses(
        self,
        *,
        engine_name: str,
        implementation_version: str,
        values: tuple[dict[str, Any], ...],
    ) -> None:
        if not values:
            return
        now = self._clock()
        base_statement = insert(AlertAnalysisStateRecord).values(
            [
                {
                    "id": self._id_factory(),
                    "engine_name": engine_name.strip().lower(),
                    "implementation_version": implementation_version,
                    "updated_at": now,
                    **value,
                }
                for value in values
            ]
        )
        statement = base_statement.on_conflict_do_update(
            index_elements=["engine_name", "implementation_version", "symbol", "horizon"],
            set_={
                "analysis_id": base_statement.excluded.analysis_id,
                "payload": base_statement.excluded.payload,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)

    async def upsert_candidates(
        self,
        *,
        engine_name: str,
        implementation_version: str,
        values: tuple[dict[str, Any], ...],
    ) -> None:
        if not values:
            return
        now = self._clock()
        base_statement = insert(AlertContinuationCandidateRecord).values(
            [
                {
                    "id": self._id_factory(),
                    "engine_name": engine_name.strip().lower(),
                    "implementation_version": implementation_version,
                    "updated_at": now,
                    **value,
                }
                for value in values
            ]
        )
        statement = base_statement.on_conflict_do_update(
            index_elements=["engine_name", "implementation_version", "symbol"],
            set_={
                "active": base_statement.excluded.active,
                "payload": base_statement.excluded.payload,
                "updated_at": now,
            },
        )
        await self._session.execute(statement)

    async def upsert_sessions(
        self,
        *,
        engine_name: str,
        implementation_version: str,
        values: tuple[dict[str, Any], ...],
    ) -> None:
        if not values:
            return
        now = self._clock()
        base_statement = insert(AlertContinuationSessionRecord).values(
            [
                {
                    "id": self._id_factory(),
                    "engine_name": engine_name.strip().lower(),
                    "implementation_version": implementation_version,
                    "updated_at": now,
                    **value,
                }
                for value in values
            ]
        )
        statement = base_statement.on_conflict_do_update(
            index_elements=["engine_name", "implementation_version", "symbol"],
            set_={
                "market_session": func.greatest(
                    AlertContinuationSessionRecord.market_session,
                    base_statement.excluded.market_session,
                ),
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

    async def load_latest(self, symbol: str) -> EntryWatchRecord | None:
        statement = (
            select(EntryWatchRecord)
            .where(EntryWatchRecord.symbol == symbol.strip().upper())
            .order_by(EntryWatchRecord.updated_at.desc())
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
        anchor_snapshot: dict[str, Any],
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
                anchor_snapshot=anchor_snapshot,
            )
            .returning(EntryWatchRecord.id)
        )
        result = await self._session.execute(statement)
        if result.scalar_one_or_none() is None:
            return False
        self._session.add(transition)
        return True

    async def update_anchor_snapshot(
        self,
        watch_id: UUID,
        *,
        status: str,
        anchor_snapshot: dict[str, Any],
    ) -> bool:
        statement = (
            update(EntryWatchRecord)
            .where(
                EntryWatchRecord.id == watch_id,
                EntryWatchRecord.status == status,
            )
            .values(anchor_snapshot=anchor_snapshot)
            .returning(EntryWatchRecord.id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None


class EntryOpportunityRepository(Repository):
    """Persist one evolving opportunity per symbol and append immutable events."""

    async def load_active(self, symbol: str) -> EntryOpportunityRecord | None:
        statement = (
            select(EntryOpportunityRecord)
            .where(
                EntryOpportunityRecord.symbol == symbol.strip().upper(),
                EntryOpportunityRecord.status != "CLOSED",
            )
            .order_by(EntryOpportunityRecord.updated_at.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)

    async def load_latest(self, symbol: str) -> EntryOpportunityRecord | None:
        statement = (
            select(EntryOpportunityRecord)
            .where(EntryOpportunityRecord.symbol == symbol.strip().upper())
            .order_by(EntryOpportunityRecord.updated_at.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)

    async def list_active(self) -> tuple[EntryOpportunityRecord, ...]:
        records = await self._session.scalars(
            select(EntryOpportunityRecord)
            .where(EntryOpportunityRecord.status != "CLOSED")
            .order_by(EntryOpportunityRecord.symbol)
        )
        return tuple(records.all())

    async def list_recent(self, *, limit: int) -> tuple[EntryOpportunityRecord, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        records = await self._session.scalars(
            select(EntryOpportunityRecord)
            .order_by(EntryOpportunityRecord.updated_at.desc())
            .limit(limit)
        )
        return tuple(records.all())

    async def event_seen(self, event_id: UUID) -> bool:
        statement = select(EntryOpportunityEventRecord.id).where(
            EntryOpportunityEventRecord.id == event_id
        )
        return await self._session.scalar(statement) is not None

    async def latest_events(
        self,
        opportunity_ids: tuple[UUID, ...],
    ) -> tuple[EntryOpportunityEventRecord, ...]:
        if not opportunity_ids:
            return ()
        records = await self._session.scalars(
            select(EntryOpportunityEventRecord)
            .where(EntryOpportunityEventRecord.opportunity_id.in_(opportunity_ids))
            .distinct(EntryOpportunityEventRecord.opportunity_id)
            .order_by(
                EntryOpportunityEventRecord.opportunity_id,
                EntryOpportunityEventRecord.occurred_at.desc(),
                EntryOpportunityEventRecord.id.desc(),
            )
        )
        return tuple(records.all())

    async def save(
        self,
        opportunity: EntryOpportunityRecord,
        event: EntryOpportunityEventRecord | None,
    ) -> bool:
        """Apply only a newer snapshot; append its event in the same transaction."""

        values = {
            column.name: getattr(opportunity, column.name)
            for column in EntryOpportunityRecord.__table__.columns
        }
        base_statement = insert(EntryOpportunityRecord).values(**values)
        statement = (
            base_statement.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "status": base_statement.excluded.status,
                    "current_maturity": base_statement.excluded.current_maturity,
                    "peak_maturity": base_statement.excluded.peak_maturity,
                    "progress_percent": base_statement.excluded.progress_percent,
                    "updated_at": base_statement.excluded.updated_at,
                    "expires_at": base_statement.excluded.expires_at,
                    "closed_at": base_statement.excluded.closed_at,
                    "close_reason": base_statement.excluded.close_reason,
                    "current_price": base_statement.excluded.current_price,
                    "revision": base_statement.excluded.revision,
                    "payload": base_statement.excluded.payload,
                },
                where=EntryOpportunityRecord.revision < base_statement.excluded.revision,
            )
            .returning(EntryOpportunityRecord.id)
        )
        result = await self._session.execute(statement)
        if result.scalar_one_or_none() is None:
            return False
        if event is not None:
            event_values = {
                column.name: getattr(event, column.name)
                for column in EntryOpportunityEventRecord.__table__.columns
            }
            await self._session.execute(
                insert(EntryOpportunityEventRecord)
                .values(**event_values)
                .on_conflict_do_nothing(index_elements=["id"])
            )
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
