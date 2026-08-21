from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    AnalysisHorizon,
    EntryLegStatus,
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
    SwingTradeMaturity,
)
from app.entry_opportunity_engine import (
    EntryOpportunityEngineV4,
    InMemoryEntryOpportunityStore,
)

NOW = datetime(2026, 8, 20, 14, 45, tzinfo=UTC)


def swing_signal(
    stage: SwingTradeMaturity | None,
    *,
    at: datetime = NOW,
    setup_id: str = "swing-trade:AAPL:L:H:1.0.0",
) -> EntrySignal:
    return EntrySignal(
        family=EntrySignalFamily.SWING_TRADE,
        swing_trade_maturity=stage,
        symbol="AAPL",
        created_at=at,
        setup_id=setup_id,
        entry_price=Decimal("97"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("95.28"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        targets=(Decimal("119"), Decimal("144.72")),
        policy_id="swing-trade",
        policy_version="1.0.0",
        reasons=("test",),
    )


def core_signal() -> EntrySignal:
    return EntrySignal(
        family=EntrySignalFamily.CORE_ENTRY,
        maturity=EntryMaturityLevel.L2,
        symbol="AAPL",
        created_at=NOW,
        setup_id="core:AAPL",
        entry_price=Decimal("97"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("92"),
        targets=(Decimal("119"),),
        policy_id="core-entry",
        policy_version="1.0.0",
        reasons=("test",),
    )


@pytest.mark.asyncio
async def test_st1_st2_watch_and_st3_opens_paper_then_st4_checkpoints() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV4(store=store)

    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST1))
    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST2, at=NOW + timedelta(minutes=15))
    )
    tracking = await store.load_active("AAPL")
    assert tracking is not None
    assert tracking.current_maturity is EntryMaturityLevel.ARMED
    assert tracking.peak_maturity is EntryMaturityLevel.ARMED
    assert tracking.legs[0].status is EntryLegStatus.WATCHING

    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST3, at=NOW + timedelta(minutes=30))
    )
    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST4, at=NOW + timedelta(minutes=45))
    )
    opened = await store.load_active("AAPL")
    assert opened is not None
    assert opened.legs[0].status is EntryLegStatus.OPEN
    reference = next(
        item for item in opened.signal_references if item.family is EntrySignalFamily.SWING_TRADE
    )
    assert reference.current_st is SwingTradeMaturity.ST4
    assert reference.peak_st is SwingTradeMaturity.ST4
    assert [
        item.swing_trade_maturity
        for item in opened.checkpoints
        if item.signal_family is EntrySignalFamily.SWING_TRADE
    ] == [
        SwingTradeMaturity.ST1,
        SwingTradeMaturity.ST2,
        SwingTradeMaturity.ST3,
        SwingTradeMaturity.ST4,
    ]


@pytest.mark.asyncio
async def test_direct_st4_opens_without_fabricating_prior_checkpoints() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV4(store=store)

    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST4))

    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.legs[0].status is EntryLegStatus.OPEN
    assert [item.swing_trade_maturity for item in opportunity.checkpoints] == [
        SwingTradeMaturity.ST4
    ]


@pytest.mark.asyncio
async def test_preentry_thesis_loss_closes_tracking_but_not_open_trade() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV4(store=store)
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST1))
    await engine.ingest_signal(swing_signal(None, at=NOW + timedelta(minutes=15)))
    assert await store.load_active("AAPL") is None

    await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            at=NOW + timedelta(minutes=30),
            setup_id="swing-trade:AAPL:L2:H2:1.0.0",
        )
    )
    await engine.ingest_signal(
        swing_signal(
            None,
            at=NOW + timedelta(minutes=45),
            setup_id="swing-trade:AAPL:L2:H2:1.0.0",
        )
    )
    opened = await store.load_active("AAPL")
    assert opened is not None
    assert opened.legs[0].status is EntryLegStatus.OPEN


@pytest.mark.asyncio
async def test_swing_trade_coexists_without_changing_core_l1_l4_maturity() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV4(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST4, at=NOW + timedelta(minutes=15))
    )

    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.current_maturity is EntryMaturityLevel.L2
    assert opportunity.peak_maturity is EntryMaturityLevel.L2
    reference = next(
        item
        for item in opportunity.signal_references
        if item.family is EntrySignalFamily.SWING_TRADE
    )
    assert reference.current_st is SwingTradeMaturity.ST4


@pytest.mark.asyncio
async def test_st3_resets_a_new_ten_session_trade_ttl() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV4(store=store)
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST1))
    tracking = await store.load_active("AAPL")
    assert tracking is not None

    entered_at = NOW + timedelta(days=3)
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST3, at=entered_at))
    opened = await store.load_active("AAPL")

    assert opened is not None
    assert opened.expires_at > tracking.expires_at
    weekdays = 0
    cursor = entered_at
    while cursor < opened.expires_at:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            weekdays += 1
    assert weekdays == 10
