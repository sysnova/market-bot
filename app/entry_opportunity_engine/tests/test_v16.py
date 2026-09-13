"""Recovery owns its Swing thesis; analytical exits need a recent market mark."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisVerdict,
    EntryCheckpointStatus,
    EntryLegStatus,
    EntrySignal,
    EntrySignalFamily,
    EntryWatchStatus,
    PatternDirection,
)
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_ownership_v10 import core_avoid, market_bar
from app.entry_opportunity_engine.tests.test_swing_trade_v4 import (
    NOW,
    core_signal,
    unrelated_watcher_invalidation,
)
from app.entry_opportunity_engine.v15 import EntryOpportunityEngineV15
from app.entry_opportunity_engine.v16 import EntryOpportunityEngineV16


def recovery() -> EntrySignal:
    return core_signal().model_copy(
        update={
            "family": EntrySignalFamily.CORE_RECOVERY,
            "setup_id": "recovery:AAPL",
            "horizons": (AnalysisHorizon.SWING, AnalysisHorizon.VOLUME_STRUCTURE),
        }
    )


@pytest.mark.parametrize("version,closes", [(15, True), (16, False)])
async def test_long_failure_preserves_recovery_only_in_new_version(
    version: int, closes: bool
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = (EntryOpportunityEngineV16 if version == 16 else EntryOpportunityEngineV15)(
        store=store
    )
    await engine.ingest_signal(recovery())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM), now=NOW + timedelta(minutes=15)
    )
    assert (await store.load_active("AAPL") is None) is closes


async def test_swing_failure_closes_recovery_including_volume_leg() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(recovery())
    await engine.ingest_analysis(core_avoid(AnalysisHorizon.SWING), now=NOW + timedelta(minutes=15))
    assert await store.load_active("AAPL") is None
    latest = await store.load_latest("AAPL")
    assert latest is not None
    assert all(leg.status is EntryLegStatus.INVALIDATED for leg in latest.legs)


async def test_core_entry_still_exits_on_long_failure() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM), now=NOW + timedelta(minutes=15)
    )
    assert await store.load_active("AAPL") is None


async def test_old_analysis_uses_recent_bar_price() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_bar(market_bar(close="99"))
    events = await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, minutes=1, price="94"),
        now=NOW + timedelta(minutes=21),
    )
    assert events[0].opportunity.checkpoints[0].current_price == Decimal("99")


async def test_stale_price_defers_exit_until_bar_even_after_restart() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, minutes=1, price="94"),
        now=NOW + timedelta(minutes=15),
    )
    assert await store.load_active("AAPL") is not None
    restarted = EntryOpportunityEngineV16(store=store)
    await restarted.ingest_bar(market_bar(close="99"))
    latest = await store.load_latest("AAPL")
    assert latest is not None
    assert await store.load_active("AAPL") is None
    assert latest.checkpoints[0].current_price == Decimal("99")
    assert latest.checkpoints[0].outcome is EntryLegStatus.THESIS_BROKEN


@pytest.mark.parametrize(
    "low,high,outcome",
    [
        ("91", "99", EntryLegStatus.INVALIDATED),
        ("96", "120", EntryLegStatus.TARGET_HIT),
    ],
)
async def test_recovery_keeps_own_stop_and_target(
    low: str, high: str, outcome: EntryLegStatus
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(recovery())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM), now=NOW + timedelta(minutes=15)
    )
    await engine.ingest_bar(market_bar(low=low, high=high))
    latest = await store.load_latest("AAPL")
    assert latest is not None
    assert latest.checkpoints[0].outcome is outcome


async def test_mixed_core_closes_while_recovery_survives_original_stop() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_signal(
        recovery().model_copy(
            update={
                "created_at": NOW + timedelta(minutes=1),
                "invalidation": Decimal("85"),
            }
        )
    )
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM), now=NOW + timedelta(minutes=15)
    )
    await engine.ingest_bar(market_bar(low="91", close="93"))
    active = await store.load_active("AAPL")
    assert active is not None
    assert all(
        cp.status is EntryCheckpointStatus.CLOSED
        for cp in active.checkpoints
        if cp.signal_family is EntrySignalFamily.CORE_ENTRY
    )
    assert any(
        cp.status is EntryCheckpointStatus.OPEN
        for cp in active.checkpoints
        if cp.signal_family is EntrySignalFamily.CORE_RECOVERY
    )


@pytest.mark.parametrize("status", [EntryWatchStatus.INVALIDATED, EntryWatchStatus.EXPIRED])
async def test_matching_long_watcher_cannot_close_recovery(status: EntryWatchStatus) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    terminal = unrelated_watcher_invalidation(at=NOW + timedelta(minutes=15))
    await engine.ingest_transition(
        terminal.model_copy(
            update={
                "occurred_at": NOW,
                "status": EntryWatchStatus.ARMED,
                "transition_id": terminal.watch_id,
                "previous_status": None,
            }
        )
    )
    await engine.ingest_signal(
        recovery().model_copy(
            update={
                "created_at": NOW + timedelta(minutes=1),
            }
        )
    )
    await engine.ingest_transition(terminal.model_copy(update={"status": status}))
    active = await store.load_active("AAPL")
    assert active is not None
    assert any(
        cp.status is EntryCheckpointStatus.OPEN
        for cp in active.checkpoints
        if cp.signal_family is EntrySignalFamily.CORE_RECOVERY
    )


async def test_new_favorable_evidence_supersedes_deferred_exit() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, minutes=1), now=NOW + timedelta(minutes=15)
    )
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, minutes=2).model_copy(
            update={
                "analysis_id": uuid4(),
                "direction": PatternDirection.BULLISH,
                "verdict": AnalysisVerdict.CAUTION,
            }
        ),
        now=NOW + timedelta(minutes=16),
    )
    await engine.ingest_bar(market_bar())
    assert await store.load_active("AAPL") is not None


@pytest.mark.parametrize("change", ["preentry", "future", "wrong_engine"])
async def test_noncausal_or_unowned_swing_evidence_cannot_exit(change: str) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(recovery())
    result = core_avoid(AnalysisHorizon.SWING)
    if change == "preentry":
        result = result.model_copy(update={"as_of": NOW - timedelta(minutes=1)})
    elif change == "future":
        result = result.model_copy(update={"as_of": NOW + timedelta(minutes=30)})
    else:
        result = result.model_copy(update={"engine_id": "other-engine"})
    await engine.ingest_analysis(result, now=NOW + timedelta(minutes=15))
    await engine.ingest_bar(market_bar())
    assert await store.load_active("AAPL") is not None


async def test_price_stop_remains_active_even_on_bearish_long_context() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(recovery())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, price="90"), now=NOW + timedelta(minutes=15)
    )
    latest = await store.load_latest("AAPL")
    assert latest is not None
    assert latest.checkpoints[0].outcome is EntryLegStatus.INVALIDATED
    assert await store.load_active("AAPL") is None


async def test_recovery_still_expires() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    created = await engine.ingest_signal(recovery())
    await engine.reconcile(now=created[0].opportunity.expires_at, active_symbols=("AAPL",))
    assert await store.load_active("AAPL") is None


async def test_unchanged_long_context_does_not_append_repeated_warning_events() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(recovery())
    first = await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM), now=NOW + timedelta(minutes=15)
    )
    assert first[0].reasons == ("core_recovery_long_bearish_context",)
    repeated = await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, minutes=16).model_copy(
            update={"analysis_id": uuid4()}
        ),
        now=NOW + timedelta(minutes=16),
    )
    assert repeated == ()
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.latest_analyses[0].as_of == NOW + timedelta(minutes=16)


@pytest.mark.parametrize("age_seconds,closes", [(300, True), (301, False)])
async def test_analytical_exit_mark_age_boundary(age_seconds: int, closes: bool) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, minutes=1),
        now=NOW + timedelta(minutes=1, seconds=age_seconds),
    )
    assert (await store.load_active("AAPL") is None) is closes


async def test_stop_precedes_deferred_analytical_exit_and_duplicate_bar_is_ignored() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV16(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.LONG_TERM, minutes=1), now=NOW + timedelta(minutes=15)
    )
    await engine.ingest_bar(market_bar(low="91", close="93"))
    latest = await store.load_latest("AAPL")
    assert latest is not None
    assert latest.checkpoints[0].current_price == Decimal("92")
    assert await engine.ingest_bar(market_bar(low="91", close="93")) == ()
