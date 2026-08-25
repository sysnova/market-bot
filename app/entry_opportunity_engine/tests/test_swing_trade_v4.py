from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    AnalysisHorizon,
    BarTimeframe,
    EntryCheckpointStatus,
    EntryLegStatus,
    EntryMaturityLevel,
    EntryOpportunityStatus,
    EntrySignal,
    EntrySignalFamily,
    MarketBar,
    SwingTradeMaturity,
)
from app.entry_opportunity_engine import (
    EntryOpportunityEngineV4,
    EntryOpportunityEngineV5,
    EntryOpportunityEngineV6,
    EntryOpportunityEngineV7,
    EntryOpportunityEngineV8,
    InMemoryEntryOpportunityStore,
)

NOW = datetime(2026, 8, 20, 14, 45, tzinfo=UTC)


def swing_signal(
    stage: SwingTradeMaturity | None,
    *,
    at: datetime = NOW,
    setup_id: str = "swing-trade:AAPL:L:H:1.0.0",
    price: str = "97",
    invalidation: str = "92",
    policy_version: str = "1.0.0",
) -> EntrySignal:
    return EntrySignal(
        family=EntrySignalFamily.SWING_TRADE,
        swing_trade_maturity=stage,
        symbol="AAPL",
        created_at=at,
        setup_id=setup_id,
        entry_price=Decimal(price),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("95.28"),
        zone_high=Decimal("100"),
        invalidation=Decimal(invalidation),
        targets=(Decimal("119"), Decimal("144.72")),
        policy_id="swing-trade",
        policy_version=policy_version,
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
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST2, at=NOW + timedelta(minutes=15)))
    tracking = await store.load_active("AAPL")
    assert tracking is not None
    assert tracking.current_maturity is EntryMaturityLevel.ARMED
    assert tracking.peak_maturity is EntryMaturityLevel.ARMED
    assert tracking.legs[0].status is EntryLegStatus.WATCHING

    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST3, at=NOW + timedelta(minutes=30)))
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST4, at=NOW + timedelta(minutes=45)))
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
async def test_swing_trade_accepts_structural_invalidation_inside_fibonacci_zone() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV4(store=store)
    signal = EntrySignal(
        family=EntrySignalFamily.SWING_TRADE,
        swing_trade_maturity=SwingTradeMaturity.ST3,
        symbol="AAPL",
        created_at=NOW,
        setup_id="swing-trade:AAPL:L:H:1.0.0",
        entry_price=Decimal("99"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("95.28"),
        zone_high=Decimal("100"),
        invalidation=Decimal("96.75"),
        targets=(Decimal("119"), Decimal("144.72")),
        policy_id="swing-trade",
        policy_version="1.0.0",
        reasons=("swing_trade_st3",),
    )

    await engine.ingest_signal(signal)

    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.invalidation == Decimal("96.75")
    assert opportunity.legs[0].status is EntryLegStatus.OPEN


@pytest.mark.asyncio
async def test_v7_preentry_thesis_loss_closes_legacy_tracking() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV7(store=store)
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST1))

    closed = await engine.ingest_signal(
        swing_signal(None, at=NOW + timedelta(minutes=15), price="99")
    )

    assert closed[0].reasons[0] == "swing_trade_preentry_ineligible"
    assert closed[0].opportunity.status is EntryOpportunityStatus.CLOSED
    assert await store.load_active("AAPL") is None


@pytest.mark.asyncio
async def test_preentry_thesis_loss_suspends_tracking_and_same_setup_resumes() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV8(store=store)
    created = await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST1))
    opportunity_id = created[0].opportunity.opportunity_id

    suspended = await engine.ingest_signal(
        swing_signal(None, at=NOW + timedelta(minutes=15), price="99")
    )

    assert suspended[0].reasons[0] == "swing_trade_preentry_ineligible_deferred"
    tracking = await store.load_active("AAPL")
    assert tracking is not None
    assert tracking.opportunity_id == opportunity_id
    assert tracking.status is EntryOpportunityStatus.CONFIRMING
    assert tracking.current_price == Decimal("99")
    assert tracking.legs[0].status is EntryLegStatus.WATCHING
    assert tracking.checkpoints[0].status is EntryCheckpointStatus.OPEN
    assert tracking.signal_references[0].current_st is None
    assert tracking.signal_references[0].peak_st is SwingTradeMaturity.ST1

    resumed = await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST1, at=NOW + timedelta(minutes=30), price="97")
    )

    assert len(resumed) == 1
    tracking = await store.load_active("AAPL")
    assert tracking is not None
    assert tracking.opportunity_id == opportunity_id
    assert len(tracking.legs) == 1
    assert len(tracking.checkpoints) == 1
    assert tracking.signal_references[0].current_st is SwingTradeMaturity.ST1


@pytest.mark.asyncio
async def test_v8_keeps_parallel_preentry_snapshot_while_another_leg_is_open() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV8(store=store)
    opened_setup = "swing-trade:AAPL:L1:H:1.2.0"
    watching_setup = "swing-trade:AAPL:L2:H:1.2.0"
    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST3, setup_id=opened_setup)
    )
    await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST1,
            at=NOW + timedelta(minutes=15),
            setup_id=watching_setup,
        )
    )

    suspended = await engine.ingest_signal(
        swing_signal(
            None,
            at=NOW + timedelta(minutes=30),
            setup_id=watching_setup,
            price="99",
        )
    )

    assert suspended[0].reasons[0] == "swing_trade_preentry_ineligible_deferred"
    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.status is EntryOpportunityStatus.OPEN
    assert opportunity.legs[0].status is EntryLegStatus.OPEN
    watching_reference = next(
        item
        for item in opportunity.signal_references
        if item.setup_id == "swing-trade:AAPL:L2:H"
    )
    assert watching_reference.current_st is None
    assert watching_reference.peak_st is SwingTradeMaturity.ST1
    watching_checkpoint = next(
        item
        for item in opportunity.checkpoints
        if item.setup_id == "swing-trade:AAPL:L2:H"
    )
    assert watching_checkpoint.status is EntryCheckpointStatus.OPEN


@pytest.mark.asyncio
async def test_v8_ignores_ineligibility_for_an_untracked_swing_setup() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV8(store=store)
    created = await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST1))
    opportunity_id = created[0].opportunity.opportunity_id

    events = await engine.ingest_signal(
        swing_signal(
            None,
            at=NOW + timedelta(minutes=15),
            setup_id="swing-trade:AAPL:OTHER:ANCHOR:1.2.0",
        )
    )

    assert events == ()
    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.opportunity_id == opportunity_id
    assert opportunity.status is EntryOpportunityStatus.CONFIRMING


@pytest.mark.asyncio
async def test_thesis_loss_does_not_close_open_trade() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV8(store=store)

    await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            setup_id="swing-trade:AAPL:L2:H2:1.0.0",
        )
    )
    await engine.ingest_signal(
        swing_signal(
            None,
            at=NOW + timedelta(minutes=15),
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
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST4, at=NOW + timedelta(minutes=15)))

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


@pytest.mark.asyncio
async def test_v5_keeps_ignoring_a_new_swing_setup_while_a_paper_leg_is_open() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(swing_signal(SwingTradeMaturity.ST3))

    events = await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            at=NOW + timedelta(minutes=15),
            setup_id="swing-trade:AAPL:L:H:1.2.0",
            policy_version="1.2.0",
        )
    )

    opportunity = await store.load_active("AAPL")
    assert events == ()
    assert opportunity is not None
    assert len(opportunity.legs) == 1


@pytest.mark.asyncio
async def test_v6_opens_and_tracks_a_parallel_leg_for_a_new_swing_thesis() -> None:
    store = InMemoryEntryOpportunityStore()
    previous_engine = EntryOpportunityEngineV5(store=store)
    engine = EntryOpportunityEngineV6(store=store)
    old_setup = "swing-trade:AAPL:L:H:1.0.0"
    new_setup = "swing-trade:AAPL:L:H:1.2.0"
    await previous_engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            setup_id=old_setup,
            invalidation="92",
        )
    )
    events = await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            at=NOW + timedelta(minutes=15),
            setup_id=new_setup,
            price="98",
            invalidation="90",
            policy_version="1.2.0",
        )
    )

    opportunity = await store.load_active("AAPL")
    assert len(events) == 1
    assert opportunity is not None
    assert [(leg.setup_id, leg.status) for leg in opportunity.legs] == [
        (old_setup, EntryLegStatus.OPEN),
        (new_setup, EntryLegStatus.OPEN),
    ]
    assert opportunity.legs[0].expires_at is not None
    assert opportunity.legs[1].expires_at is not None
    assert opportunity.legs[1].expires_at > opportunity.legs[0].expires_at

    await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST4,
            at=NOW + timedelta(minutes=30),
            setup_id=new_setup,
            price="99",
            invalidation="90",
            policy_version="1.2.0",
        )
    )
    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert len(opportunity.legs) == 2

    await engine.ingest_bar(
        MarketBar(
            symbol="AAPL",
            timeframe=BarTimeframe.MINUTE_1,
            timestamp=NOW + timedelta(minutes=31),
            open=Decimal("98"),
            high=Decimal("100"),
            low=Decimal("91"),
            close=Decimal("96"),
            volume=Decimal("1000"),
            source="fixture",
            feed="sip",
            is_final=True,
        )
    )
    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.status.value == "OPEN"
    assert [leg.status for leg in opportunity.legs] == [
        EntryLegStatus.INVALIDATED,
        EntryLegStatus.OPEN,
    ]


@pytest.mark.asyncio
async def test_v6_expires_each_parallel_swing_leg_on_its_own_ttl() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV6(store=store)
    old_setup = "swing-trade:AAPL:L:H:1.0.0"
    new_setup = "swing-trade:AAPL:L:H:1.2.0"
    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST3, setup_id=old_setup)
    )
    await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            at=NOW + timedelta(days=2),
            setup_id=new_setup,
            policy_version="1.2.0",
        )
    )
    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    old_expiry = opportunity.legs[0].expires_at
    assert old_expiry is not None

    events = await engine.reconcile(now=old_expiry, active_symbols=("AAPL",))

    opportunity = await store.load_active("AAPL")
    assert len(events) == 1
    assert opportunity is not None
    assert [leg.status for leg in opportunity.legs] == [
        EntryLegStatus.EXPIRED,
        EntryLegStatus.OPEN,
    ]


@pytest.mark.asyncio
async def test_v7_updates_same_structure_across_strategy_versions_without_new_leg() -> None:
    store = InMemoryEntryOpportunityStore()
    old_setup = "swing-trade:AAPL:L:H:1.0.0"
    new_setup = "swing-trade:AAPL:L:H:1.2.0"
    await EntryOpportunityEngineV6(store=store).ingest_signal(
        swing_signal(SwingTradeMaturity.ST3, setup_id=old_setup)
    )

    events = await EntryOpportunityEngineV7(store=store).ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            at=NOW + timedelta(minutes=15),
            setup_id=new_setup,
            price="98",
            policy_version="1.2.0",
        )
    )

    opportunity = await store.load_active("AAPL")
    assert len(events) == 1
    assert opportunity is not None
    assert [item.setup_id for item in opportunity.signal_references] == [
        "swing-trade:AAPL:L:H"
    ]
    assert opportunity.signal_references[0].policy_version == "1.2.0"
    assert [item.setup_id for item in opportunity.legs] == ["swing-trade:AAPL:L:H"]
    assert len(opportunity.legs) == 1
    assert len(opportunity.checkpoints) == 1
    assert opportunity.checkpoints[0].entry_price == Decimal("97")


@pytest.mark.asyncio
async def test_v7_consolidates_historical_preentry_duplicates_then_advances_once() -> None:
    store = InMemoryEntryOpportunityStore()
    legacy = EntryOpportunityEngineV6(store=store)
    await legacy.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST1,
            setup_id="swing-trade:AAPL:L:H:1.0.0",
            price="96",
        )
    )
    await legacy.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST1,
            at=NOW + timedelta(minutes=15),
            setup_id="swing-trade:AAPL:L:H:1.1.0",
            price="97",
            policy_version="1.1.0",
        )
    )

    before = await store.load_active("AAPL")
    assert before is not None
    assert len(before.signal_references) == 2
    assert len(before.checkpoints) == 2

    await EntryOpportunityEngineV7(store=store).ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST2,
            at=NOW + timedelta(minutes=30),
            setup_id="swing-trade:AAPL:L:H",
            price="98",
            policy_version="1.2.0",
        )
    )

    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert len(opportunity.signal_references) == 1
    assert opportunity.signal_references[0].setup_id == "swing-trade:AAPL:L:H"
    assert [item.swing_trade_maturity for item in opportunity.checkpoints] == [
        SwingTradeMaturity.ST1,
        SwingTradeMaturity.ST2,
    ]
    assert opportunity.checkpoints[0].entry_price == Decimal("96")


@pytest.mark.asyncio
async def test_v7_keeps_materially_different_anchor_structure_as_parallel_thesis() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV7(store=store)
    first_setup = "swing-trade:AAPL:L1:H:1.2.0"
    second_setup = "swing-trade:AAPL:L2:H:1.2.0"
    await engine.ingest_signal(
        swing_signal(SwingTradeMaturity.ST3, setup_id=first_setup)
    )
    await engine.ingest_signal(
        swing_signal(
            SwingTradeMaturity.ST3,
            at=NOW + timedelta(minutes=15),
            setup_id=second_setup,
            price="98",
        )
    )

    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert [item.setup_id for item in opportunity.legs] == [
        "swing-trade:AAPL:L1:H",
        "swing-trade:AAPL:L2:H",
    ]
