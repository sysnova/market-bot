from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

import app.integration.entry_watch_store as store_module
from app.contracts import AnalysisHorizon, EntryWatchStatus, EntryWatchTransition, EventEnvelope
from app.entry_watcher import EntryWatch
from app.integration.entry_watch_store import (
    PostgresEntryWatchStore,
    _to_domain,
    _to_record,
    _transition_record,
)

NOW = datetime(2026, 7, 26, 15, tzinfo=UTC)
WATCH_ID = UUID("0195f3a5-9000-7000-8000-000000000001")
ANALYSIS_ID = UUID("0195f3a5-9000-7000-8000-000000000002")


def watch() -> EntryWatch:
    return EntryWatch(
        watch_id=WATCH_ID,
        symbol="AAPL",
        status=EntryWatchStatus.ARMED,
        armed_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(weeks=8),
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        original_price=Decimal("120"),
        current_price=Decimal("120"),
        correction_target_percent=Decimal("12.5000"),
        source_analysis_id=ANALYSIS_ID,
        source_context_hash="sha256:" + "a" * 64,
        anchor_snapshot={"classification": "extended"},
    )


@pytest.mark.unit
def test_postgres_record_round_trip_preserves_frozen_thesis() -> None:
    original = watch()

    restored = _to_domain(_to_record(original))

    assert restored == original


@pytest.mark.unit
def test_transition_record_is_json_serializable() -> None:
    transition = EntryWatchTransition(
        watch_id=WATCH_ID,
        symbol="AAPL",
        status=EntryWatchStatus.ARMED,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("120"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("long_entry_thesis_armed",),
        horizons=(AnalysisHorizon.LONG_TERM,),
        source_analysis_ids=(ANALYSIS_ID,),
    )

    record = _transition_record(transition)

    assert record.status == "ARMED"
    assert record.horizons == ["LONG_TERM"]
    assert record.source_analysis_ids == [str(ANALYSIS_ID)]


@pytest.mark.unit
async def test_readiness_requires_both_versioned_tables() -> None:
    session = AsyncMock()
    session.scalar.side_effect = ["market_bot.entry_watches", None, "market_bot.outbox_events"]
    context = AsyncMock()
    context.__aenter__.return_value = session
    factory = MagicMock(return_value=context)
    store = PostgresEntryWatchStore(factory)  # type: ignore[arg-type]

    assert await store.is_ready() is False
    assert session.scalar.await_count == 3


@pytest.mark.unit
async def test_transition_persists_the_updated_anchor_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SimpleNamespace(apply_transition=AsyncMock(return_value=True))

    class FakeUnitOfWork:
        async def __aenter__(self) -> FakeUnitOfWork:
            self.entry_watches = repository
            self.outbox = SimpleNamespace(enqueue=AsyncMock())
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(store_module, "PersistenceUnitOfWork", lambda _: FakeUnitOfWork())
    store = PostgresEntryWatchStore(MagicMock())
    updated = watch().model_copy(
        update={
            "status": EntryWatchStatus.IN_ZONE,
            "current_price": Decimal("103"),
            "anchor_snapshot": {
                "classification": "extended",
                "zone_touched_at": NOW.isoformat(),
            },
        }
    )
    transition = EntryWatchTransition(
        watch_id=WATCH_ID,
        symbol="AAPL",
        previous_status=EntryWatchStatus.ARMED,
        status=EntryWatchStatus.IN_ZONE,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("103"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("target_zone_reached",),
        horizons=(AnalysisHorizon.SWING,),
        source_analysis_ids=(ANALYSIS_ID,),
    )

    await store.transition(updated, transition)

    assert repository.apply_transition.await_args.kwargs["anchor_snapshot"] == {
        "classification": "extended",
        "zone_touched_at": NOW.isoformat(),
    }


@pytest.mark.unit
async def test_create_persists_transition_and_outbox_envelope_in_one_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SimpleNamespace(add=MagicMock())
    outbox = SimpleNamespace(enqueue=AsyncMock())

    class FakeUnitOfWork:
        async def __aenter__(self) -> FakeUnitOfWork:
            self.entry_watches = repository
            self.outbox = outbox
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(store_module, "PersistenceUnitOfWork", lambda _: FakeUnitOfWork())
    store = PostgresEntryWatchStore(MagicMock(), source="entry-watcher-v5")
    transition = EntryWatchTransition(
        watch_id=WATCH_ID,
        symbol="AAPL",
        status=EntryWatchStatus.ARMED,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("120"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("long_entry_thesis_armed",),
        horizons=(AnalysisHorizon.LONG_TERM,),
        source_analysis_ids=(ANALYSIS_ID,),
    )

    await store.create(watch(), transition)

    repository.add.assert_called_once()
    queued = outbox.enqueue.await_args.kwargs
    assert queued["aggregate_type"] == "entry-watch"
    assert queued["aggregate_id"] == str(WATCH_ID)
    assert queued["subject"] == "marketbot.v1.entry-watch.transition.ARMED.AAPL"
    envelope = EventEnvelope.model_validate(queued["payload"], strict=False)
    assert envelope.source == "entry-watcher-v5"
    assert envelope.payload["transition_id"] == str(transition.transition_id)
    assert outbox.enqueue.await_count == 1


@pytest.mark.unit
async def test_triggered_transition_enqueues_only_the_watcher_fact() -> None:
    outbox = SimpleNamespace(enqueue=AsyncMock())
    store = PostgresEntryWatchStore(MagicMock(), source="entry-watcher-v5")
    transition = EntryWatchTransition(
        watch_id=WATCH_ID,
        symbol="AAPL",
        previous_status=EntryWatchStatus.IN_ZONE,
        status=EntryWatchStatus.TRIGGERED,
        occurred_at=NOW,
        zone_low=Decimal("100"),
        zone_high=Decimal("105"),
        invalidation=Decimal("92"),
        current_price=Decimal("106"),
        watch_expires_at=NOW + timedelta(weeks=8),
        reasons=("entry_reconfirmed",),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        source_analysis_ids=(ANALYSIS_ID,),
    )

    await store._enqueue(outbox, transition)  # pyright: ignore[reportPrivateUsage]

    outbox.enqueue.assert_awaited_once()
    transition_call = outbox.enqueue.await_args.kwargs
    assert transition_call["subject"] == "marketbot.v1.entry-watch.transition.TRIGGERED.AAPL"
