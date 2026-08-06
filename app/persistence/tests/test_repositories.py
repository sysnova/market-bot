"""Repository behavior tests with an isolated fake session."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.persistence import (
    LongPortfolioAlertRecord,
    PatreonCapsTransitionRecord,
    PatreonCapsWatchRecord,
)
from app.persistence.repositories import (
    CheckpointRepository,
    EntryWatchRepository,
    EventPayloadConflictError,
    HealthRepository,
    LongPortfolioAlertRepository,
    OutboxRepository,
    PatreonCapsRepository,
    ProcessedEventRepository,
)

ENTITY_ID = UUID("0195f3a5-9000-7000-8000-000000000001")
NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)


class ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class ScalarListResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


@pytest.mark.unit
@pytest.mark.asyncio
async def test_processed_event_record_reports_new_delivery() -> None:
    session = AsyncMock()
    session.execute.return_value = ScalarResult(ENTITY_ID)
    repository = ProcessedEventRepository(
        session,
        id_factory=lambda: ENTITY_ID,
        clock=lambda: NOW,
    )

    inserted = await repository.record(
        consumer_name="reference-engine",
        event_id=ENTITY_ID,
        subject="market.synthetic",
        payload_hash="sha256:" + "a" * 64,
    )

    assert inserted is True
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))
    assert "ON CONFLICT (consumer_name, event_id) DO NOTHING" in sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_processed_event_record_reports_duplicate_delivery() -> None:
    session = AsyncMock()
    payload_hash = "sha256:" + "b" * 64
    session.execute.side_effect = [ScalarResult(None), ScalarResult(payload_hash)]
    repository = ProcessedEventRepository(session)

    inserted = await repository.record(
        consumer_name="reference-engine",
        event_id=ENTITY_ID,
        subject="market.synthetic",
        payload_hash=payload_hash,
    )

    assert inserted is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_processed_event_rejects_duplicate_with_different_payload() -> None:
    session = AsyncMock()
    session.execute.side_effect = [
        ScalarResult(None),
        ScalarResult("sha256:" + "c" * 64),
    ]
    repository = ProcessedEventRepository(session)

    with pytest.raises(EventPayloadConflictError, match="payload hash conflict"):
        await repository.record(
            consumer_name="reference-engine",
            event_id=ENTITY_ID,
            subject="market.synthetic",
            payload_hash="sha256:" + "d" * 64,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_claim_uses_skip_locked() -> None:
    session = AsyncMock()
    session.scalars.return_value = ScalarListResult([])
    repository = OutboxRepository(session)

    claimed = await repository.claim_pending(limit=10, now=NOW)

    assert claimed == []
    statement = session.scalars.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "published_at IS NULL" in sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_enqueue_uses_injected_id_and_clock() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    repository = OutboxRepository(session, id_factory=lambda: ENTITY_ID, clock=lambda: NOW)

    event = await repository.enqueue(
        aggregate_type="run",
        aggregate_id="run-1",
        event_type="run.started",
        subject="control.run.started",
        payload={"run_id": "run-1"},
    )

    assert event.id == ENTITY_ID
    assert event.occurred_at == NOW
    session.add.assert_called_once_with(event)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_rejects_nonpositive_claim_size() -> None:
    repository = OutboxRepository(AsyncMock())

    with pytest.raises(ValueError, match="positive"):
        await repository.claim_pending(limit=0, now=NOW)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outbox_publish_and_failure_updates_are_scoped_to_unpublished() -> None:
    session = AsyncMock()
    repository = OutboxRepository(session, clock=lambda: NOW)

    await repository.mark_published(ENTITY_ID)
    await repository.record_failure(ENTITY_ID, error="nats unavailable")

    assert session.execute.await_count == 2
    published_sql = str(
        session.execute.await_args_list[0].args[0].compile(dialect=repository.dialect)
    )
    failure_sql = str(
        session.execute.await_args_list[1].args[0].compile(dialect=repository.dialect)
    )
    assert "published_at IS NULL" in published_sql
    assert "published_at IS NULL" in failure_sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_checkpoint_advance_is_monotonic_upsert() -> None:
    session = AsyncMock()
    repository = CheckpointRepository(session, id_factory=lambda: ENTITY_ID, clock=lambda: NOW)

    await repository.advance(consumer_name="reference-engine", stream="MARKET", sequence=41)

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))
    assert "ON CONFLICT (consumer_name, stream) DO UPDATE" in sql
    assert "greatest" in sql.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_checkpoint_rejects_negative_sequence() -> None:
    repository = CheckpointRepository(AsyncMock())

    with pytest.raises(ValueError, match="nonnegative"):
        await repository.advance(consumer_name="reference-engine", stream="MARKET", sequence=-1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_upsert_replaces_latest_snapshot() -> None:
    session = AsyncMock()
    repository = HealthRepository(session, id_factory=lambda: ENTITY_ID, clock=lambda: NOW)

    await repository.upsert(
        service_name="reference-engine",
        status="HEALTHY",
        details={"nats": "connected"},
        observed_at=NOW,
    )

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))
    assert "ON CONFLICT (service_name) DO UPDATE" in sql


@pytest.mark.unit
async def test_entry_watch_loads_only_active_symbol_thesis() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    repository = EntryWatchRepository(session)

    assert await repository.load_active("aapl") is None
    statement = session.scalar.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))

    assert "entry_watches.symbol" in sql
    assert "entry_watches.status IN" in sql
    assert "ORDER BY" in sql


@pytest.mark.unit
async def test_entry_watch_loads_latest_thesis_regardless_of_status() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    repository = EntryWatchRepository(session)

    assert await repository.load_latest("aapl") is None
    statement = session.scalar.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))

    assert "entry_watches.symbol" in sql
    assert "entry_watches.status IN" not in sql
    assert "entry_watches.updated_at DESC" in sql


@pytest.mark.unit
async def test_entry_watch_transition_uses_optimistic_status_guard() -> None:
    session = AsyncMock()
    session.execute.return_value = ScalarResult(ENTITY_ID)
    session.add = MagicMock()
    repository = EntryWatchRepository(session)
    transition = MagicMock()

    changed = await repository.apply_transition(
        ENTITY_ID,
        previous_status="ARMED",
        status="IN_ZONE",
        current_price=Decimal("103"),
        updated_at=NOW,
        terminal_at=None,
        transition=transition,
    )

    assert changed is True
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))
    assert "entry_watches.status =" in sql
    assert "RETURNING" in sql
    session.add.assert_called_once_with(transition)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_long_portfolio_alert_insert_is_idempotent_by_deduplication_key() -> None:
    session = AsyncMock()
    session.execute.return_value = ScalarResult(ENTITY_ID)
    repository = LongPortfolioAlertRepository(session)
    mapped = LongPortfolioAlertRecord(
        id=ENTITY_ID,
        deduplication_key="long-portfolio:1.0.0:HIMS:1",
        symbol="HIMS",
        created_at=NOW,
        expires_at=None,
        rule_version="1.0.0",
        horizon_end=NOW.date(),
        current_price=Decimal("50"),
        buy_zone_low=Decimal("48"),
        buy_zone_high=Decimal("51"),
        invalidation=Decimal("43"),
        target_weight_percent=Decimal("11.73"),
        target_capital_usd=Decimal("12081.90"),
        tranche_percent=Decimal("50"),
        tranche_usd=Decimal("6040.95"),
        suggested_whole_shares=Decimal("120"),
        score=Decimal("82"),
        reasons=["solid_long_entry"],
        payload={"symbol": "HIMS"},
        persisted_at=NOW,
    )

    assert await repository.add(mapped) is True
    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=repository.dialect))
    assert "ON CONFLICT (deduplication_key) DO NOTHING" in sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_patreon_caps_save_upserts_snapshot_and_deduplicates_transition() -> None:
    session = AsyncMock()
    session.execute.side_effect = [MagicMock(), ScalarResult(ENTITY_ID)]
    repository = PatreonCapsRepository(session)
    watch = PatreonCapsWatchRecord(
        id=ENTITY_ID,
        symbol="NVO",
        rule_version="1.0.0",
        state="WATCH_ZONE",
        armed_at=NOW,
        updated_at=NOW,
        expires_at=NOW,
        zone_low=Decimal("48"),
        zone_center=Decimal("49"),
        zone_high=Decimal("50"),
        invalidation=Decimal("46"),
        highest_price=Decimal("50"),
        tranche_stage=0,
        saw_macro_shock=False,
        support_sources=["pivot_daily", "avwap"],
        source_analysis_ids=[],
        payload={"state": "WATCH_ZONE"},
        created_at=NOW,
    )
    transition = PatreonCapsTransitionRecord(
        id=ENTITY_ID,
        deduplication_key="patreon-caps:1.0.0:NVO:WATCH_ZONE:0:1",
        watch_id=ENTITY_ID,
        symbol="NVO",
        previous_state=None,
        state="WATCH_ZONE",
        occurred_at=NOW,
        rule_version="1.0.0",
        current_price=Decimal("49"),
        patreon_score=Decimal("75"),
        tranche_stage=None,
        suggested_tranche_usd=None,
        suggested_whole_shares=None,
        payload={"state": "WATCH_ZONE"},
        persisted_at=NOW,
    )

    assert await repository.save(watch, transition) is True
    assert session.execute.await_count == 2
    transition_statement = session.execute.await_args_list[1].args[0]
    sql = str(transition_statement.compile(dialect=repository.dialect))
    assert "ON CONFLICT (deduplication_key) DO NOTHING" in sql
