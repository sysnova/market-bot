"""Independent recovery theses must survive Core trend invalidations."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    BarTimeframe,
    EntryCheckpointStatus,
    EntryLegStatus,
    EntrySignalFamily,
    EntryWatchStatus,
    GeriCountertrendMaturity,
    MarketBar,
    SwingTradeMaturity,
)
from app.entry_opportunity_engine import (
    EntryOpportunityEngineV10 as EngineUnderTest,
)
from app.entry_opportunity_engine import InMemoryEntryOpportunityStore
from app.entry_opportunity_engine.tests.test_swing_trade_v4 import (
    NOW,
    core_signal,
    long_avoid,
    swing_signal,
    unrelated_watcher_invalidation,
)


def core_avoid(horizon: AnalysisHorizon, *, minutes: int = 15, price: str = "98") -> AnalysisResult:
    return long_avoid(at=NOW + timedelta(minutes=minutes), price=price).model_copy(
        update={
            "engine_id": "swing" if horizon is AnalysisHorizon.SWING else "long-term",
            "horizon": horizon,
        }
    )


@pytest.mark.parametrize("horizon", [AnalysisHorizon.SWING, AnalysisHorizon.LONG_TERM])
@pytest.mark.parametrize("minutes", [-16, 15])
@pytest.mark.parametrize(
    "family", [EntrySignalFamily.SWING_TRADE, EntrySignalFamily.GERI_COUNTERTREND]
)
async def test_core_verdict_cannot_close_independent_recovery(
    family: EntrySignalFamily, horizon: AnalysisHorizon, minutes: int
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    signal = swing_signal(SwingTradeMaturity.ST3)
    if family is EntrySignalFamily.GERI_COUNTERTREND:
        signal = signal.model_copy(
            update={
                "family": family,
                "swing_trade_maturity": None,
                "countertrend_maturity": GeriCountertrendMaturity.CT2,
            }
        )
    await engine.ingest_signal(signal)
    events = await engine.ingest_analysis(
        core_avoid(horizon, minutes=minutes), now=NOW + timedelta(minutes=16)
    )
    active = await store.load_active("AAPL")
    assert events == ()
    assert active is not None
    assert all(cp.status is EntryCheckpointStatus.OPEN for cp in active.checkpoints)
    assert active.legs[0].status is EntryLegStatus.OPEN
    assert active.latest_analyses[0].direction.value == "BEARISH"


@pytest.mark.parametrize("horizon", [AnalysisHorizon.SWING, AnalysisHorizon.LONG_TERM])
async def test_mixed_opportunity_closes_core_only_and_preserves_exit_reason(
    horizon: AnalysisHorizon,
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST3, at=NOW + timedelta(minutes=1)))
    events = await engine.ingest_analysis(core_avoid(horizon), now=NOW + timedelta(minutes=15))
    assert events
    active = await store.load_active("AAPL")
    assert active is not None
    core = [cp for cp in active.checkpoints if cp.signal_family is EntrySignalFamily.CORE_ENTRY]
    swing = [cp for cp in active.checkpoints if cp.signal_family is EntrySignalFamily.SWING_TRADE]
    assert all(cp.status is EntryCheckpointStatus.CLOSED for cp in core)
    assert all(
        cp.outcome in {EntryLegStatus.INVALIDATED, EntryLegStatus.THESIS_BROKEN} for cp in core
    )
    assert all(cp.status is EntryCheckpointStatus.OPEN for cp in swing)
    assert any(leg.status is EntryLegStatus.OPEN for leg in active.legs)


async def test_core_horizon_invalidation_is_not_reported_as_time_exit() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    await engine.ingest_signal(core_signal())
    events = await engine.ingest_analysis(
        core_avoid(AnalysisHorizon.SWING), now=NOW + timedelta(minutes=15)
    )
    assert events
    assert await store.load_active("AAPL") is None
    assert events[0].opportunity.checkpoints[0].outcome is EntryLegStatus.INVALIDATED


@pytest.mark.parametrize("price", ["98", "80"])
async def test_preentry_core_analysis_cannot_close_new_entry_or_overwrite_price(price: str) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    await engine.ingest_signal(core_signal())
    assert (
        await engine.ingest_analysis(
            core_avoid(AnalysisHorizon.SWING, minutes=-16, price=price),
            now=NOW + timedelta(seconds=3),
        )
        == ()
    )
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.current_price == Decimal("97")
    assert active.checkpoints[0].status is EntryCheckpointStatus.OPEN


@pytest.mark.parametrize("status", [EntryWatchStatus.INVALIDATED, EntryWatchStatus.EXPIRED])
async def test_matching_core_watcher_terminal_event_preserves_swing_trade(
    status: EntryWatchStatus,
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
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
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST3, at=NOW + timedelta(minutes=1)))
    await engine.ingest_transition(terminal.model_copy(update={"status": status}))
    active = await store.load_active("AAPL")
    assert active is not None
    assert (
        next(
            cp for cp in active.checkpoints if cp.signal_family is EntrySignalFamily.SWING_TRADE
        ).status
        is EntryCheckpointStatus.OPEN
    )


def market_bar(*, low: str = "96", high: str = "99", close: str = "98") -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=NOW + timedelta(minutes=20),
        open=Decimal("97"),
        low=Decimal(low),
        high=Decimal(high),
        close=Decimal(close),
        volume=Decimal("1000"),
        is_final=True,
        source="fixture",
        feed="sip",
    )


async def test_core_stop_does_not_close_geri_checkpoint_without_a_leg() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST3).model_copy(
            update={
                "family": EntrySignalFamily.GERI_COUNTERTREND,
                "swing_trade_maturity": None,
                "countertrend_maturity": GeriCountertrendMaturity.CT2,
                "setup_id": "geri:AAPL:test",
                "invalidation": Decimal("85"),
            }
        )
    )
    await engine.ingest_analysis(core_avoid(AnalysisHorizon.SWING), now=NOW + timedelta(minutes=15))
    await engine.ingest_bar(market_bar(low="91", close="93"))
    active = await store.load_active("AAPL")
    assert active is not None
    geri = next(
        cp for cp in active.checkpoints if cp.signal_family is EntrySignalFamily.GERI_COUNTERTREND
    )
    assert geri.status is EntryCheckpointStatus.OPEN


@pytest.mark.parametrize(
    "low,high,outcome",
    [
        ("91", "99", EntryLegStatus.INVALIDATED),
        ("96", "120", EntryLegStatus.TARGET_HIT),
    ],
)
async def test_swing_trade_keeps_own_stop_and_target(
    low: str,
    high: str,
    outcome: EntryLegStatus,
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST3))
    await engine.ingest_analysis(core_avoid(AnalysisHorizon.SWING), now=NOW + timedelta(minutes=15))
    await engine.ingest_bar(market_bar(low=low, high=high))
    closed = await store.load_latest("AAPL")
    assert closed is not None
    assert closed.checkpoints[0].outcome is outcome
    assert await store.load_active("AAPL") is None


async def test_swing_trade_keeps_own_expiry() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    created = await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST3))
    await engine.reconcile(now=created[0].opportunity.expires_at, active_symbols=("AAPL",))
    closed = await store.load_latest("AAPL")
    assert closed is not None
    assert closed.checkpoints[0].outcome is EntryLegStatus.EXPIRED
    assert await store.load_active("AAPL") is None


@pytest.mark.parametrize(
    "family", [EntrySignalFamily.SWING_TRADE, EntrySignalFamily.GERI_COUNTERTREND]
)
async def test_core_added_after_recovery_does_not_share_its_horizon_leg(
    family: EntrySignalFamily,
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EngineUnderTest(store=store)
    signal = swing_signal(SwingTradeMaturity.ST3)
    if family is EntrySignalFamily.GERI_COUNTERTREND:
        signal = signal.model_copy(
            update={
                "family": family,
                "swing_trade_maturity": None,
                "countertrend_maturity": GeriCountertrendMaturity.CT2,
            }
        )
    created = await engine.ingest_signal(signal)
    original_leg = created[0].opportunity.legs[0]
    await engine.ingest_signal(
        core_signal().model_copy(
            update={
                "created_at": NOW + timedelta(minutes=1),
            }
        )
    )
    await engine.ingest_analysis(core_avoid(AnalysisHorizon.SWING), now=NOW + timedelta(minutes=15))
    active = await store.load_active("AAPL")
    assert active is not None
    assert next(leg for leg in active.legs if leg.leg_id == original_leg.leg_id) == original_leg
    assert len(active.legs) == 2
    assert sum(leg.status is EntryLegStatus.OPEN for leg in active.legs) == 1
    assert (
        next(cp for cp in active.checkpoints if cp.signal_family is family).status
        is EntryCheckpointStatus.OPEN
    )
