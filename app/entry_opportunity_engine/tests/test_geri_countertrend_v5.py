from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryCheckpointStatus,
    EntryCloseReason,
    EntryLegStatus,
    EntryMaturityLevel,
    EntrySignal,
    EntrySignalFamily,
    EntryWatchStatus,
    EntryWatchTransition,
    GeriCountertrendMaturity,
    MarketBar,
    NamedValue,
    PatternDirection,
)
from app.entry_opportunity_engine import (
    EntryOpportunityEngineV5,
    InMemoryEntryOpportunityStore,
)

NOW = datetime(2026, 8, 20, 14, 45, tzinfo=UTC)


def countertrend_signal(
    stage: GeriCountertrendMaturity | None,
    *,
    at: datetime = NOW,
    price: str = "85",
    setup_id: str = "geri-countertrend:AAPL:2026-08-20T14:30:00Z:1.3.0",
    reasons: tuple[str, ...] = ("countertrend_setup_eligible",),
) -> EntrySignal:
    return EntrySignal(
        family=EntrySignalFamily.GERI_COUNTERTREND,
        countertrend_maturity=stage,
        symbol="AAPL",
        created_at=at,
        setup_id=setup_id,
        entry_price=Decimal(price),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("80"),
        zone_high=Decimal("82"),
        invalidation=Decimal("78"),
        targets=(Decimal("95"),),
        policy_id="geri-countertrend",
        policy_version="1.3.0",
        reasons=reasons,
    )


def core_signal() -> EntrySignal:
    return EntrySignal(
        family=EntrySignalFamily.CORE_ENTRY,
        maturity=EntryMaturityLevel.L2,
        symbol="AAPL",
        created_at=NOW,
        setup_id="core:AAPL",
        entry_price=Decimal("81"),
        horizons=(AnalysisHorizon.SWING,),
        zone_low=Decimal("80"),
        zone_high=Decimal("82"),
        invalidation=Decimal("75"),
        targets=(Decimal("100"),),
        policy_id="core-entry",
        policy_version="1.0.0",
        reasons=("core_l2",),
    )


def minute(*, low: str, high: str, close: str, at: datetime) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=at,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
        is_final=True,
    )


@pytest.mark.asyncio
async def test_ct0_watches_ct1_opens_and_ct2_ct4_add_measurement_checkpoints() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)

    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT0))
    tracking = await store.load_active("AAPL")
    assert tracking is not None
    assert tracking.legs[0].status is EntryLegStatus.WATCHING

    for offset, stage in enumerate(
        (
            GeriCountertrendMaturity.CT1,
            GeriCountertrendMaturity.CT2,
            GeriCountertrendMaturity.CT3,
            GeriCountertrendMaturity.CT4,
        ),
        start=1,
    ):
        await engine.ingest_signal(
            countertrend_signal(
                stage,
                at=NOW + timedelta(minutes=15 * offset),
                price=str(81 + offset / 10),
            )
        )

    opened = await store.load_active("AAPL")
    assert opened is not None
    assert opened.legs[0].status is EntryLegStatus.OPEN
    assert opened.legs[0].opened_at == NOW + timedelta(minutes=15)
    reference = next(
        item
        for item in opened.signal_references
        if item.family is EntrySignalFamily.GERI_COUNTERTREND
    )
    assert reference.current_ct is GeriCountertrendMaturity.CT4
    assert reference.peak_ct is GeriCountertrendMaturity.CT4
    assert [
        item.countertrend_maturity
        for item in opened.checkpoints
        if item.signal_family is EntrySignalFamily.GERI_COUNTERTREND
    ] == list(GeriCountertrendMaturity)


@pytest.mark.asyncio
async def test_direct_ct4_opens_without_fabricating_prior_stages() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)

    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT4, price="81"))

    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.legs[0].status is EntryLegStatus.OPEN
    assert [item.countertrend_maturity for item in opportunity.checkpoints] == [
        GeriCountertrendMaturity.CT4
    ]


@pytest.mark.asyncio
async def test_preentry_loss_closes_tracking_but_pullback_after_ct1_stays_open() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT0))
    await engine.ingest_signal(
        countertrend_signal(
            None,
            at=NOW + timedelta(minutes=15),
            price="79",
            reasons=("countertrend_reclaim_required",),
        )
    )
    assert await store.load_active("AAPL") is None

    await engine.ingest_signal(
        countertrend_signal(
            GeriCountertrendMaturity.CT1,
            at=NOW + timedelta(minutes=30),
            price="81",
            setup_id="geri-countertrend:AAPL:new:1.3.0",
        )
    )
    await engine.ingest_signal(
        countertrend_signal(
            None,
            at=NOW + timedelta(minutes=45),
            price="79",
            setup_id="geri-countertrend:AAPL:new:1.3.0",
            reasons=("countertrend_reclaim_required",),
        )
    )
    opened = await store.load_active("AAPL")
    assert opened is not None
    assert opened.legs[0].status is EntryLegStatus.OPEN


@pytest.mark.asyncio
async def test_favorable_preentry_rr_loss_waits_until_regular_session_close() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT0, price="81"))

    intraday = await engine.ingest_signal(
        countertrend_signal(
            None,
            at=NOW + timedelta(minutes=15),
            price="90",
            reasons=("insufficient_reward_risk", "countertrend_ineligible"),
        )
    )

    assert "geri_countertrend_preentry_ineligible_deferred" in intraday[0].reasons
    active = await store.load_active("AAPL")
    assert active is not None
    reference = next(
        item
        for item in active.signal_references
        if item.family is EntrySignalFamily.GERI_COUNTERTREND
    )
    assert reference.current_ct is GeriCountertrendMaturity.CT0

    close_at = datetime(2026, 8, 20, 19, 59, tzinfo=UTC)
    closed_events = await engine.ingest_signal(
        countertrend_signal(
            None,
            at=close_at,
            price="90",
            reasons=(
                "insufficient_reward_risk",
                "countertrend_ineligible",
                "regular_session_close",
            ),
        )
    )

    assert len(closed_events) == 1
    closed = closed_events[0].opportunity
    assert closed.close_reason is EntryCloseReason.POLICY_INELIGIBLE
    assert closed.checkpoints[0].outcome is EntryLegStatus.TIME_EXIT
    assert "geri_countertrend_preentry_ineligible_at_session_close" in closed_events[0].reasons


@pytest.mark.asyncio
async def test_countertrend_cannot_be_created_outside_regular_session() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)

    events = await engine.ingest_signal(
        countertrend_signal(
            GeriCountertrendMaturity.CT0,
            at=datetime(2026, 8, 20, 4, 49, tzinfo=UTC),
        )
    )

    assert events == ()
    assert await store.load_active("AAPL") is None


@pytest.mark.asyncio
async def test_ct0_target_is_terminal_before_preentry_policy_loss() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT0, price="81"))

    events = await engine.ingest_signal(
        countertrend_signal(
            None,
            at=NOW + timedelta(minutes=15),
            price="95",
            reasons=("countertrend_target_reached",),
        )
    )

    assert len(events) == 1
    assert events[0].opportunity.close_reason is EntryCloseReason.ALL_HORIZONS_CLOSED
    assert events[0].opportunity.checkpoints[0].outcome is EntryLegStatus.TARGET_HIT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bar", "expected_status"),
    [
        (
            minute(
                low="77",
                high="81",
                close="78",
                at=NOW + timedelta(minutes=16),
            ),
            EntryLegStatus.INVALIDATED,
        ),
        (
            minute(
                low="81",
                high="96",
                close="95",
                at=NOW + timedelta(minutes=16),
            ),
            EntryLegStatus.TARGET_HIT,
        ),
    ],
)
async def test_ct1_paper_trade_closes_and_records_pl(
    bar: MarketBar, expected_status: EntryLegStatus
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT1, price="81"))

    events = await engine.ingest_bar(bar)

    assert len(events) == 1
    closed = events[0].opportunity
    assert closed.status.value == "CLOSED"
    assert closed.legs[0].status is expected_status
    ct1 = next(
        item
        for item in closed.checkpoints
        if item.countertrend_maturity is GeriCountertrendMaturity.CT1
    )
    assert ct1.status is EntryCheckpointStatus.CLOSED
    assert ct1.gain_loss_percent is not None


@pytest.mark.asyncio
async def test_long_term_bearish_avoid_is_context_not_invalidation_for_countertrend() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT1, price="81"))
    result = AnalysisResult(
        analysis_id=UUID("0195f3a5-9000-7000-8000-000000000071"),
        engine_id="long-term",
        engine_version="2.0.0",
        symbol="AAPL",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=NOW - timedelta(days=1),
        verdict=AnalysisVerdict.AVOID,
        direction=PatternDirection.BEARISH,
        score=Decimal("25.75"),
        confidence=Decimal("0.2575"),
        reasons=("bearish_structure",),
        metrics=(NamedValue(name="reference_price", value=Decimal("81")),),
        context_hash="sha256:" + "a" * 64,
    )

    events = await engine.ingest_analysis(result, now=NOW + timedelta(minutes=16))

    assert events == ()
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.legs[0].status is EntryLegStatus.OPEN
    assert active.latest_analyses == (result,)


@pytest.mark.asyncio
async def test_price_below_invalidation_still_closes_countertrend_on_analysis() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT1, price="81"))
    result = AnalysisResult(
        analysis_id=UUID("0195f3a5-9000-7000-8000-000000000072"),
        engine_id="long-term",
        engine_version="2.0.0",
        symbol="AAPL",
        horizon=AnalysisHorizon.LONG_TERM,
        as_of=NOW + timedelta(minutes=16),
        verdict=AnalysisVerdict.AVOID,
        direction=PatternDirection.BEARISH,
        score=Decimal("20"),
        confidence=Decimal("0.8"),
        reasons=("bearish_structure",),
        metrics=(NamedValue(name="reference_price", value=Decimal("77")),),
        context_hash="sha256:" + "b" * 64,
    )

    events = await engine.ingest_analysis(result, now=NOW + timedelta(minutes=16))

    assert len(events) == 1
    assert events[0].opportunity.close_reason is EntryCloseReason.ORIGINAL_THESIS_INVALIDATED
    assert "original_invalidation_breached" in events[0].reasons


@pytest.mark.asyncio
@pytest.mark.parametrize("status", (EntryWatchStatus.INVALIDATED, EntryWatchStatus.EXPIRED))
async def test_unrelated_watcher_terminal_cannot_close_standalone_countertrend(
    status: EntryWatchStatus,
) -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT1, price="81"))
    transition = EntryWatchTransition(
        transition_id=UUID("0195f3a5-9000-7000-8000-000000000084"),
        watch_id=UUID("0195f3a5-9000-7000-8000-000000000021"),
        symbol="AAPL",
        previous_status=EntryWatchStatus.ARMED,
        status=status,
        occurred_at=NOW + timedelta(minutes=16),
        zone_low=Decimal("80"),
        zone_high=Decimal("82"),
        invalidation=Decimal("78"),
        current_price=Decimal("81"),
        watch_expires_at=NOW + timedelta(days=5),
        reasons=("long_structure_invalidated",),
        horizons=(AnalysisHorizon.LONG_TERM,),
        source_analysis_ids=(UUID("0195f3a5-9000-7000-8000-000000000071"),),
    )

    events = await engine.ingest_transition(transition)

    assert events == ()
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.legs[0].status is EntryLegStatus.OPEN


@pytest.mark.asyncio
async def test_countertrend_tracking_keeps_a_five_session_paper_ttl() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(countertrend_signal(GeriCountertrendMaturity.CT0))
    entered_at = NOW + timedelta(days=1)
    await engine.ingest_signal(
        countertrend_signal(GeriCountertrendMaturity.CT1, at=entered_at, price="81")
    )
    opened = await store.load_active("AAPL")
    assert opened is not None

    weekdays = 0
    cursor = NOW
    while cursor < opened.expires_at:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            weekdays += 1
    assert weekdays == 5


@pytest.mark.asyncio
async def test_countertrend_coexists_with_core_without_changing_l_maturity() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_signal(
        countertrend_signal(
            GeriCountertrendMaturity.CT1,
            at=NOW + timedelta(minutes=15),
            price="81",
        )
    )

    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert opportunity.current_maturity is EntryMaturityLevel.L2
    assert opportunity.peak_maturity is EntryMaturityLevel.L2
    assert any(
        item.countertrend_maturity is GeriCountertrendMaturity.CT1
        for item in opportunity.checkpoints
    )

    events = await engine.ingest_bar(
        minute(
            low="80",
            high="96",
            close="95",
            at=NOW + timedelta(minutes=16),
        )
    )
    assert len(events) == 1
    assert "geri_countertrend_ct1_target_hit" in events[0].reasons
    assert events[0].opportunity.current_maturity is EntryMaturityLevel.L2


@pytest.mark.asyncio
async def test_new_setup_does_not_replace_open_countertrend_inside_core() -> None:
    store = InMemoryEntryOpportunityStore()
    engine = EntryOpportunityEngineV5(store=store)
    await engine.ingest_signal(core_signal())
    await engine.ingest_signal(
        countertrend_signal(
            GeriCountertrendMaturity.CT1,
            at=NOW + timedelta(minutes=15),
            price="81",
        )
    )

    events = await engine.ingest_signal(
        countertrend_signal(
            GeriCountertrendMaturity.CT0,
            at=NOW + timedelta(minutes=30),
            setup_id="geri-countertrend:AAPL:new-pivot:1.3.0",
        )
    )

    assert events == ()
    opportunity = await store.load_active("AAPL")
    assert opportunity is not None
    assert sum(
        item.family is EntrySignalFamily.GERI_COUNTERTREND
        for item in opportunity.signal_references
    ) == 1
