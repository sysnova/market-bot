"""PostgreSQL adapter for the Entry Watcher persistence port."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import EntryWatchStatus, EntryWatchTransition
from app.entry_watcher import EntryWatch
from app.persistence import (
    EntryWatchRecord,
    EntryWatchTransitionRecord,
    PersistenceUnitOfWork,
)


class PostgresEntryWatchStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_ready(self) -> bool:
        """Confirm connectivity and that the versioned watcher migration exists."""
        async with self._session_factory() as session:
            relation = await session.scalar(
                text("select to_regclass('market_bot.entry_watches')")
            )
            transitions = await session.scalar(
                text("select to_regclass('market_bot.entry_watch_transitions')")
            )
            return relation is not None and transitions is not None

    async def load_active(self, symbol: str) -> EntryWatch | None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            record = await unit.entry_watches.load_active(symbol)
        return _to_domain(record) if record is not None else None

    async def create(
        self, watch: EntryWatch, transition: EntryWatchTransition
    ) -> None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            unit.entry_watches.add(_to_record(watch), _transition_record(transition))

    async def transition(
        self, watch: EntryWatch, transition: EntryWatchTransition
    ) -> None:
        previous = transition.previous_status
        if previous is None:
            raise ValueError("persisted update requires a previous status")
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            changed = await unit.entry_watches.apply_transition(
                watch.watch_id,
                previous_status=previous.value,
                status=watch.status.value,
                current_price=watch.current_price,
                updated_at=watch.updated_at,
                terminal_at=watch.terminal_at,
                transition=_transition_record(transition),
            )
            if not changed:
                raise RuntimeError("entry watch transition lost an optimistic race")


def _to_record(watch: EntryWatch) -> EntryWatchRecord:
    return EntryWatchRecord(
        id=watch.watch_id,
        symbol=watch.symbol,
        status=watch.status.value,
        thesis_version="1.0.0",
        armed_at=watch.armed_at,
        updated_at=watch.updated_at,
        expires_at=watch.expires_at,
        terminal_at=watch.terminal_at,
        zone_low=watch.zone_low,
        zone_high=watch.zone_high,
        invalidation=watch.invalidation,
        original_price=watch.original_price,
        current_price=watch.current_price,
        correction_target_percent=watch.correction_target_percent,
        source_analysis_id=watch.source_analysis_id,
        source_context_hash=watch.source_context_hash,
        anchor_snapshot=watch.anchor_snapshot,
        created_at=watch.armed_at,
    )


def _to_domain(record: EntryWatchRecord) -> EntryWatch:
    return EntryWatch(
        watch_id=record.id,
        symbol=record.symbol,
        status=EntryWatchStatus(record.status),
        armed_at=record.armed_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
        zone_low=Decimal(record.zone_low),
        zone_high=Decimal(record.zone_high),
        invalidation=Decimal(record.invalidation),
        original_price=Decimal(record.original_price),
        current_price=Decimal(record.current_price),
        correction_target_percent=Decimal(record.correction_target_percent),
        source_analysis_id=record.source_analysis_id,
        source_context_hash=record.source_context_hash,
        anchor_snapshot=record.anchor_snapshot,
        terminal_at=record.terminal_at,
    )


def _transition_record(
    transition: EntryWatchTransition,
) -> EntryWatchTransitionRecord:
    return EntryWatchTransitionRecord(
        id=transition.transition_id,
        watch_id=transition.watch_id,
        previous_status=(
            transition.previous_status.value
            if transition.previous_status is not None
            else None
        ),
        status=transition.status.value,
        occurred_at=transition.occurred_at,
        current_price=transition.current_price,
        reasons=list(transition.reasons),
        horizons=[horizon.value for horizon in transition.horizons],
        source_analysis_ids=[str(value) for value in transition.source_analysis_ids],
        created_at=transition.occurred_at,
    )
