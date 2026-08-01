"""PostgreSQL adapter for PatreonCaps state and immutable transitions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import PatreonCapsTransition
from app.patreon_caps_engine import PatreonCapsEvaluation, PatreonCapsWatch
from app.persistence import (
    PatreonCapsTransitionRecord,
    PatreonCapsWatchRecord,
    PersistenceUnitOfWork,
)


class PostgresPatreonCapsStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_ready(self) -> bool:
        async with self._session_factory() as session:
            watches = await session.scalar(
                text("select to_regclass('market_bot.patreon_caps_watches')")
            )
            transitions = await session.scalar(
                text("select to_regclass('market_bot.patreon_caps_transitions')")
            )
            return watches is not None and transitions is not None

    async def load_active(self) -> tuple[PatreonCapsWatch, ...]:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            records = await unit.patreon_caps.load_active()
            return tuple(
                PatreonCapsWatch.model_validate(record.payload, strict=False)
                for record in records
            )

    async def latest_transition_times(
        self, *, rule_version: str
    ) -> dict[str, datetime]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(
                    PatreonCapsTransitionRecord.symbol,
                    func.max(PatreonCapsTransitionRecord.occurred_at),
                )
                .where(PatreonCapsTransitionRecord.rule_version == rule_version)
                .group_by(PatreonCapsTransitionRecord.symbol)
            )
            return {symbol: occurred_at for symbol, occurred_at in rows}

    async def save(self, evaluation: PatreonCapsEvaluation) -> bool:
        transition = evaluation.transition
        if transition is None:
            return False
        watch = evaluation.watch
        watch_record = PatreonCapsWatchRecord(
            id=watch.watch_id,
            symbol=watch.symbol,
            rule_version=watch.rule_version,
            state=watch.state.value,
            armed_at=watch.armed_at,
            updated_at=watch.updated_at,
            expires_at=watch.expires_at,
            zone_low=watch.zone_low,
            zone_center=watch.zone_center,
            zone_high=watch.zone_high,
            invalidation=watch.invalidation,
            highest_price=watch.highest_price,
            tranche_stage=watch.tranche_stage,
            saw_macro_shock=watch.saw_macro_shock,
            support_sources=list(watch.support_sources),
            source_analysis_ids=[str(item) for item in watch.source_analysis_ids],
            payload=watch.model_dump(mode="json"),
            created_at=watch.armed_at,
        )
        transition_record = PatreonCapsTransitionRecord(
            id=transition.transition_id,
            deduplication_key=_deduplication_key(transition),
            watch_id=transition.watch_id,
            symbol=transition.symbol,
            previous_state=(
                transition.previous_state.value if transition.previous_state is not None else None
            ),
            state=transition.state.value,
            occurred_at=transition.occurred_at,
            rule_version=transition.rule_version,
            current_price=transition.current_price,
            patreon_score=transition.patreon_score,
            tranche_stage=transition.tranche_stage,
            suggested_tranche_usd=transition.suggested_tranche_usd,
            suggested_whole_shares=transition.suggested_whole_shares,
            payload=transition.model_dump(mode="json"),
            persisted_at=transition.occurred_at,
        )
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            return await unit.patreon_caps.save(watch_record, transition_record)

    async def recent(self, *, limit: int = 50) -> tuple[PatreonCapsTransition, ...]:
        async with self._session_factory() as session:
            records = await session.scalars(
                select(PatreonCapsTransitionRecord)
                .order_by(PatreonCapsTransitionRecord.occurred_at.desc())
                .limit(limit)
            )
            return tuple(
                PatreonCapsTransition.model_validate(record.payload, strict=False)
                for record in reversed(records.all())
            )


def _deduplication_key(transition: PatreonCapsTransition) -> str:
    stage = transition.tranche_stage or 0
    return (
        f"patreon-caps:{transition.rule_version}:{transition.watch_id}:"
        f"{transition.state.value}:{stage}:{int(transition.occurred_at.timestamp())}"
    )
