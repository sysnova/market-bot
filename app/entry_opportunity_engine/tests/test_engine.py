from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryCloseReason,
    EntryLegStatus,
    EntryMaturityLevel,
    EntryOpportunityStatus,
    EntrySignal,
    EntrySignalFamily,
    EntryWatchStatus,
    EntryWatchTransition,
    LocalAlert,
    MarketBar,
    NamedValue,
    PatternDirection,
)
from app.entry_opportunity_engine import (
    EntryOpportunityEngine,
    EntryOpportunityEngineV2,
    EntryOpportunityEngineV3,
    InMemoryEntryOpportunityStore,
)

NOW = datetime(2026, 8, 6, 14, tzinfo=UTC)
HASH = "sha256:" + "a" * 64
OPPORTUNITY_ID = UUID("0195f3a5-9000-7000-8000-000000000090")


def watch_transition(
    status: EntryWatchStatus,
    *,
    watch_id: str,
    price: str,
    occurred_at: datetime = NOW,
    previous: EntryWatchStatus | None = None,
    horizons: tuple[AnalysisHorizon, ...] = (AnalysisHorizon.LONG_TERM,),
    reasons: tuple[str, ...] = ("fixture",),
    transition_id: UUID | None = None,
) -> EntryWatchTransition:
    return EntryWatchTransition(
        transition_id=transition_id
        or UUID(
            {
                EntryWatchStatus.ARMED: "0195f3a5-9000-7000-8000-000000000081",
                EntryWatchStatus.IN_ZONE: "0195f3a5-9000-7000-8000-000000000082",
                EntryWatchStatus.TRIGGERED: "0195f3a5-9000-7000-8000-000000000083",
                EntryWatchStatus.INVALIDATED: "0195f3a5-9000-7000-8000-000000000084",
                EntryWatchStatus.EXPIRED: "0195f3a5-9000-7000-8000-000000000085",
                EntryWatchStatus.EARLY_ENTRY: "0195f3a5-9000-7000-8000-000000000086",
                EntryWatchStatus.IMPULSE_EXTENDED: "0195f3a5-9000-7000-8000-000000000087",
                EntryWatchStatus.POLICY_INELIGIBLE: "0195f3a5-9000-7000-8000-000000000088",
            }[status]
        ),
        watch_id=UUID(watch_id),
        symbol="AAPL",
        previous_status=previous,
        status=status,
        occurred_at=occurred_at,
        zone_low=Decimal("95"),
        zone_high=Decimal("100"),
        invalidation=Decimal("90"),
        current_price=Decimal(price),
        watch_expires_at=NOW + timedelta(days=56),
        reasons=reasons,
        horizons=horizons,
        source_analysis_ids=(UUID("0195f3a5-9000-7000-8000-000000000011"),),
    )


def analysis(
    horizon: AnalysisHorizon,
    *,
    price: str,
    verdict: AnalysisVerdict,
    direction: PatternDirection,
    as_of: datetime,
    analysis_id: UUID | None = None,
    extra_metrics: tuple[NamedValue, ...] = (),
) -> AnalysisResult:
    metrics = [NamedValue(name="reference_price", value=Decimal(price))]
    if horizon is AnalysisHorizon.LONG_TERM:
        metrics.extend(
            (
                NamedValue(name="buy_zone_low", value=Decimal("95")),
                NamedValue(name="buy_zone_high", value=Decimal("100")),
                NamedValue(name="invalidation", value=Decimal("90")),
            )
        )
    return AnalysisResult(
        analysis_id=analysis_id or UUID("0195f3a5-9000-7000-8000-000000000071"),
        engine_id="fixture",
        engine_version="4.0.0",
        symbol="AAPL",
        horizon=horizon,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=Decimal("20") if verdict is AnalysisVerdict.AVOID else Decimal("80"),
        confidence=Decimal("0.8"),
        reasons=("fixture",),
        metrics=(*metrics, *extra_metrics),
        context_hash=HASH,
    )


def bar(*, timestamp: datetime, close: str, low: str = "102", high: str = "105") -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=timestamp,
        open=Decimal("103"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source="fixture",
        feed="sip",
        is_final=True,
    )


@pytest.mark.unit
async def test_early_watcher_entry_opens_l1_horizon_legs() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV3(store=store, id_factory=lambda: OPPORTUNITY_ID)
    watch_id = "0195f3a5-9000-7000-8000-000000000021"
    await manager.ingest_transition(
        watch_transition(EntryWatchStatus.ARMED, watch_id=watch_id, price="98")
    )

    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.EARLY_ENTRY,
            watch_id=watch_id,
            price="102",
            occurred_at=NOW + timedelta(minutes=5),
            previous=EntryWatchStatus.ARMED,
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
            reasons=("early_entry_confirmed",),
        )
    )

    active = await store.load_active("AAPL")
    assert active is not None
    assert active.status is EntryOpportunityStatus.CONFIRMING
    assert active.current_maturity is EntryMaturityLevel.L1
    opened = tuple(leg for leg in active.legs if leg.status is EntryLegStatus.OPEN)
    assert {leg.horizon for leg in opened} == {
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    }
    assert all(leg.entry_price == Decimal("102") for leg in opened)


@pytest.mark.unit
async def test_l2_anchor_replaces_original_zone_only_for_l4_retest() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV3(store=store, id_factory=lambda: OPPORTUNITY_ID)
    watch_id = "0195f3a5-9000-7000-8000-000000000021"
    await manager.ingest_transition(
        watch_transition(EntryWatchStatus.ARMED, watch_id=watch_id, price="98")
    )
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.EARLY_ENTRY,
            watch_id=watch_id,
            price="102",
            occurred_at=NOW + timedelta(minutes=5),
            previous=EntryWatchStatus.ARMED,
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        )
    )
    l2_signal = entry_signal(
        EntrySignalFamily.CORE_ENTRY,
        signal_id="0195f3a5-9000-7000-8000-000000000091",
        setup_id="aapl-l2",
        created_at=NOW + timedelta(minutes=10),
        maturity=EntryMaturityLevel.L2,
    ).model_copy(
        update={
            "entry_price": Decimal("106"),
            "zone_low": Decimal("104"),
            "zone_high": Decimal("106"),
            "invalidation": Decimal("102"),
        }
    )
    await manager.ingest_signal(l2_signal)

    active = await store.load_active("AAPL")
    assert active is not None
    assert active.zone_low == Decimal("95")
    assert active.zone_high == Decimal("100")
    l2 = next(item for item in active.checkpoints if item.level is EntryMaturityLevel.L2)
    assert l2.zone_low == Decimal("104")
    assert l2.zone_high == Decimal("106")
    assert l2.invalidation == Decimal("102")

    ignored = await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.TRIGGERED,
            watch_id=watch_id,
            price="108",
            occurred_at=NOW + timedelta(minutes=12),
            previous=EntryWatchStatus.EARLY_ENTRY,
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        )
    )
    assert ignored == ()
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.peak_maturity is EntryMaturityLevel.L2

    touch_time = NOW + timedelta(minutes=15)
    assert await manager.ingest_bar(
        bar(timestamp=touch_time, close="106", low="103", high="107")
    ) == ()
    active = await store.load_active("AAPL")
    assert active is not None
    l2 = next(item for item in active.checkpoints if item.level is EntryMaturityLevel.L2)
    assert l2.retested_at == touch_time
    assert l2.retest_low == Decimal("104")
    assert active.peak_maturity is EntryMaturityLevel.L2

    reclaim_time = NOW + timedelta(minutes=20)
    reclaim = analysis(
        AnalysisHorizon.INTRADAY,
        price="107",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        as_of=reclaim_time,
        analysis_id=UUID("0195f3a5-9000-7000-8000-000000000093"),
        extra_metrics=(
            NamedValue(name="atr14", value=Decimal("2")),
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="mature_confirmation_gate_passed", value=True),
            NamedValue(name="entry_efficiency_gate_passed", value=True),
            NamedValue(name="five_minute_higher_low", value=True),
            NamedValue(name="entry_trigger_level", value=Decimal("106.5")),
            NamedValue(name="invalidation_level", value=Decimal("103")),
            NamedValue(name="objective_level", value=Decimal("115")),
        ),
    )
    events = await manager.ingest_analysis(reclaim, now=reclaim_time)

    assert len(events) == 1
    assert "l2_zone_reclaim_confirmed" in events[0].reasons
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.peak_maturity is EntryMaturityLevel.L4
    assert active.status is EntryOpportunityStatus.OPEN
    assert active.zone_low == Decimal("95")
    assert active.zone_high == Decimal("100")
    l4 = next(item for item in active.checkpoints if item.level is EntryMaturityLevel.L4)
    assert l4.entry_price == Decimal("107")
    assert l4.invalidation == Decimal("103")


@pytest.mark.unit
async def test_extended_impulse_cannot_bootstrap_an_orphan_opportunity() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV3(store=store, id_factory=lambda: OPPORTUNITY_ID)

    events = await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.IMPULSE_EXTENDED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="111.27",
            reasons=(
                "entry_window_missed",
                "impulse_extended_awaiting_pullback",
            ),
        )
    )

    assert events == ()
    assert await store.load_active("AAPL") is None


def alert(
    level: EntryMaturityLevel,
    *,
    created_at: datetime,
) -> LocalAlert:
    definitions = {
        EntryMaturityLevel.L1: (
            AlertKind.ENTRY_CONFIRMED,
            (AnalysisHorizon.LONG_TERM, AnalysisHorizon.INTRADAY),
            "0195f3a5-9000-7000-8000-000000000061",
        ),
        EntryMaturityLevel.L2: (
            AlertKind.ENTRY_CONFIRMED,
            (AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
            "0195f3a5-9000-7000-8000-000000000062",
        ),
        EntryMaturityLevel.L3: (
            AlertKind.HIGH_CONVICTION_BUY,
            (
                AnalysisHorizon.LONG_TERM,
                AnalysisHorizon.SWING,
                AnalysisHorizon.INTRADAY,
            ),
            "0195f3a5-9000-7000-8000-000000000063",
        ),
    }
    kind, horizons, alert_id = definitions[level]
    return LocalAlert(
        alert_id=UUID(alert_id),
        symbol="AAPL",
        created_at=created_at,
        severity=AlertSeverity.ACTION,
        title=f"AAPL {level.value}",
        message="fixture maturity",
        horizons=horizons,
        component_analysis_ids=(UUID("0195f3a5-9000-7000-8000-000000000011"),),
        metrics=(
            NamedValue(name="current_price", value=Decimal("101")),
            NamedValue(name="invalidation", value=Decimal("94")),
            NamedValue(name="objective", value=Decimal("110")),
        ),
        score=Decimal("85"),
        reasons=("fixture_maturity",),
        deduplication_key=f"fixture:aapl:{level.value}",
        kind=kind,
    )


def entry_signal(
    family: EntrySignalFamily,
    *,
    signal_id: str,
    setup_id: str,
    created_at: datetime,
    maturity: EntryMaturityLevel | None = None,
    complete_levels: bool = True,
) -> EntrySignal:
    return EntrySignal(
        signal_id=UUID(signal_id),
        family=family,
        maturity=maturity,
        symbol="AAPL",
        created_at=created_at,
        setup_id=setup_id,
        entry_price=Decimal("101"),
        horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        zone_low=Decimal("98") if complete_levels else None,
        zone_high=Decimal("101") if complete_levels else None,
        invalidation=Decimal("94") if complete_levels else None,
        targets=(Decimal("110"),) if complete_levels else (),
        policy_id=f"{family.value.lower()}-policy",
        policy_version="1.0.0",
        reasons=("fixture_signal",),
    )


async def _open(manager: EntryOpportunityEngine) -> None:
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="110",
        )
    )
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.IN_ZONE,
            watch_id="0195f3a5-9000-7000-8000-000000000022",
            price="99",
            occurred_at=NOW + timedelta(minutes=1),
            previous=EntryWatchStatus.ARMED,
        )
    )
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.TRIGGERED,
            watch_id="0195f3a5-9000-7000-8000-000000000023",
            price="103",
            occurred_at=NOW + timedelta(minutes=5),
            previous=EntryWatchStatus.IN_ZONE,
            horizons=(
                AnalysisHorizon.LONG_TERM,
                AnalysisHorizon.SWING,
                AnalysisHorizon.INTRADAY,
            ),
            reasons=("price_efficient_entry_confirmed",),
        )
    )


@pytest.mark.unit
async def test_same_ticker_advances_one_opportunity_and_preserves_original_thesis() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)

    await _open(manager)

    active = await store.load_active("AAPL")
    assert active is not None
    assert active.opportunity_id == OPPORTUNITY_ID
    assert active.original_watch_id == UUID("0195f3a5-9000-7000-8000-000000000021")
    assert active.status is EntryOpportunityStatus.OPEN
    assert active.peak_maturity is EntryMaturityLevel.L4
    assert active.zone_low == Decimal("95")
    assert active.zone_high == Decimal("100")
    assert active.invalidation == Decimal("90")
    assert tuple(item.level for item in active.checkpoints) == (
        EntryMaturityLevel.ARMED,
        EntryMaturityLevel.IN_ZONE,
        EntryMaturityLevel.L4,
    )
    l4 = next(item for item in active.checkpoints if item.level is EntryMaturityLevel.L4)
    assert l4.entry_price == Decimal("103")
    assert {item.horizon for item in active.legs} == {
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    }
    assert all(item.entry_price == Decimal("103") for item in active.legs)
    assert len(store.opportunities) == 1


@pytest.mark.unit
async def test_v3_current_maturity_regresses_while_peak_is_preserved() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV3(store=store, id_factory=lambda: OPPORTUNITY_ID)
    watch_id = "0195f3a5-9000-7000-8000-000000000021"
    await manager.ingest_transition(
        watch_transition(EntryWatchStatus.ARMED, watch_id=watch_id, price="110")
    )
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.IN_ZONE,
            watch_id=watch_id,
            price="99",
            occurred_at=NOW + timedelta(minutes=1),
            previous=EntryWatchStatus.ARMED,
        )
    )

    events = await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id=watch_id,
            price="103",
            occurred_at=NOW + timedelta(minutes=2),
            previous=EntryWatchStatus.IN_ZONE,
            transition_id=UUID("0195f3a5-9000-7000-8000-000000000086"),
        )
    )

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.ARMED
    assert active.current_maturity is EntryMaturityLevel.ARMED
    assert active.peak_maturity is EntryMaturityLevel.IN_ZONE
    assert active.progress_percent == Decimal("20")


@pytest.mark.unit
async def test_orphan_triggered_recovers_open_l4_opportunity_idempotently() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    transition = watch_transition(
        EntryWatchStatus.TRIGGERED,
        watch_id="0195f3a5-9000-7000-8000-000000000023",
        price="103",
        previous=EntryWatchStatus.IN_ZONE,
        horizons=(
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        ),
        reasons=("price_efficient_entry_confirmed",),
    )

    events = await manager.ingest_transition(transition)

    assert len(events) == 1
    assert events[0].reasons == (
        "opportunity_recovered_from_triggered",
        "price_efficient_entry_confirmed",
    )
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.opportunity_id == OPPORTUNITY_ID
    assert active.original_watch_id == transition.watch_id
    assert active.status is EntryOpportunityStatus.OPEN
    assert active.current_maturity is EntryMaturityLevel.L4
    assert active.peak_maturity is EntryMaturityLevel.L4
    assert active.progress_percent == Decimal("100")
    assert active.zone_low == transition.zone_low
    assert active.zone_high == transition.zone_high
    assert active.invalidation == transition.invalidation
    assert active.source_analysis_ids == transition.source_analysis_ids
    assert {item.horizon for item in active.legs} == set(transition.horizons)
    assert all(item.status is EntryLegStatus.OPEN for item in active.legs)

    assert await manager.ingest_transition(transition) == ()
    assert len(store.opportunities) == 1
    assert len(store.events) == 1


@pytest.mark.unit
async def test_buy_alerts_advance_l1_l2_l3_on_the_same_opportunity() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="110",
        )
    )

    for offset, level in enumerate(
        (EntryMaturityLevel.L1, EntryMaturityLevel.L2, EntryMaturityLevel.L3),
        start=1,
    ):
        await manager.ingest_alert(alert(level, created_at=NOW + timedelta(minutes=offset)))

    active = await store.load_active("AAPL")
    assert active is not None
    assert active.status is EntryOpportunityStatus.CONFIRMING
    assert active.peak_maturity is EntryMaturityLevel.L3
    assert active.progress_percent == Decimal("90")
    assert active.zone_low == Decimal("95")
    assert active.invalidation == Decimal("90")
    assert tuple(item.level for item in active.checkpoints) == (
        EntryMaturityLevel.ARMED,
        EntryMaturityLevel.L1,
        EntryMaturityLevel.L2,
        EntryMaturityLevel.L3,
    )
    assert {item.horizon for item in active.legs if item.status is EntryLegStatus.OPEN} == {
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    }
    assert active.invalidation == Decimal("90")
    assert all(
        item.invalidation == Decimal("94") and item.target == Decimal("110")
        for item in active.legs
        if item.status is EntryLegStatus.OPEN
    )
    assert next(
        item for item in active.checkpoints if item.level is EntryMaturityLevel.L1
    ).target == Decimal("110")
    revision = active.revision

    assert (
        await manager.ingest_alert(
            alert(EntryMaturityLevel.L3, created_at=NOW + timedelta(minutes=3))
        )
        == ()
    )
    assert (await store.load_active("AAPL")).revision == revision  # type: ignore[union-attr]


@pytest.mark.unit
async def test_session_close_closes_intraday_leg_but_keeps_swing_and_long_open() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await _open(manager)

    events = await manager.ingest_bar(
        bar(timestamp=datetime(2026, 8, 6, 19, 59, tzinfo=UTC), close="104")
    )

    active = await store.load_active("AAPL")
    assert active is not None and active.status is EntryOpportunityStatus.OPEN
    legs = {item.horizon: item for item in active.legs}
    assert legs[AnalysisHorizon.INTRADAY].status is EntryLegStatus.SESSION_CLOSED
    assert legs[AnalysisHorizon.INTRADAY].gain_loss_percent == Decimal("0.9709")
    assert legs[AnalysisHorizon.SWING].status is EntryLegStatus.OPEN
    assert legs[AnalysisHorizon.LONG_TERM].status is EntryLegStatus.OPEN
    assert any("intraday_session_closed" in event.reasons for event in events)


@pytest.mark.unit
async def test_market_bar_cursor_makes_recovery_idempotent() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    processed_at = NOW + timedelta(minutes=30)
    await manager.ingest_bar(bar(timestamp=processed_at, close="104"))
    processed = await store.load_active("AAPL")
    assert processed is not None
    assert processed.last_market_bar_at == processed_at

    await manager.ingest_bar(
        bar(timestamp=processed_at, close="99", low="98", high="105")
    )
    await manager.ingest_bar(
        bar(timestamp=processed_at - timedelta(minutes=1), close="98", low="97", high="104")
    )

    recovered = await store.load_active("AAPL")
    assert recovered == processed


@pytest.mark.unit
async def test_delayed_triggered_is_not_dropped_after_a_newer_bar() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    latest_bar_at = NOW + timedelta(minutes=30)
    await manager.ingest_bar(bar(timestamp=latest_bar_at, close="104"))

    events = await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.TRIGGERED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="103",
            occurred_at=NOW + timedelta(minutes=5),
            previous=EntryWatchStatus.IN_ZONE,
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
        )
    )

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.OPEN
    assert active.updated_at == latest_bar_at
    assert active.current_price == Decimal("104")
    assert {leg.entry_price for leg in active.legs if leg.status is EntryLegStatus.OPEN} == {
        Decimal("103")
    }


@pytest.mark.unit
async def test_delayed_alert_is_not_dropped_after_a_newer_bar() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    latest_bar_at = NOW + timedelta(minutes=30)
    await manager.ingest_bar(bar(timestamp=latest_bar_at, close="104"))

    events = await manager.ingest_alert(
        alert(EntryMaturityLevel.L1, created_at=NOW + timedelta(minutes=5))
    )

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.CONFIRMING
    assert active.updated_at == latest_bar_at
    assert active.current_price == Decimal("104")
    assert {leg.entry_price for leg in active.legs if leg.status is EntryLegStatus.OPEN} == {
        Decimal("101")
    }


@pytest.mark.unit
async def test_older_watcher_event_is_rejected_by_its_causal_stream_cursor() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.IN_ZONE,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="99",
            occurred_at=NOW + timedelta(minutes=10),
            previous=EntryWatchStatus.ARMED,
        )
    )
    before = await store.load_active("AAPL")
    assert before is not None

    events = await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="101",
            occurred_at=NOW + timedelta(minutes=5),
            transition_id=UUID("0195f3a5-9000-7000-8000-000000000086"),
        )
    )

    after = await store.load_active("AAPL")
    assert events == ()
    assert after == before


@pytest.mark.unit
async def test_bearish_analysis_closes_a_confirming_horizon_leg() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    await manager.ingest_alert(alert(EntryMaturityLevel.L1, created_at=NOW + timedelta(minutes=1)))

    events = await manager.ingest_analysis(
        analysis(
            AnalysisHorizon.INTRADAY,
            price="99",
            verdict=AnalysisVerdict.CAUTION,
            direction=PatternDirection.BEARISH,
            as_of=NOW + timedelta(minutes=2),
        ),
        now=NOW + timedelta(minutes=2),
    )

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.CONFIRMING
    legs = {item.horizon: item for item in active.legs}
    assert legs[AnalysisHorizon.INTRADAY].status is EntryLegStatus.INVALIDATED
    assert legs[AnalysisHorizon.LONG_TERM].status is EntryLegStatus.OPEN


@pytest.mark.unit
async def test_bar_closes_opportunity_when_every_opened_leg_reaches_target() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    await manager.ingest_alert(alert(EntryMaturityLevel.L1, created_at=NOW + timedelta(minutes=1)))

    events = await manager.ingest_bar(
        bar(
            timestamp=NOW + timedelta(minutes=2),
            close="110",
            low="100",
            high="111",
        )
    )

    assert await store.load_active("AAPL") is None
    closed = await store.load_latest("AAPL")
    assert closed is not None
    assert closed.status is EntryOpportunityStatus.CLOSED
    assert closed.close_reason is EntryCloseReason.ALL_HORIZONS_CLOSED
    assert all(
        leg.status is EntryLegStatus.TARGET_HIT for leg in closed.legs if leg.opened_at is not None
    )
    assert len(events) == 1
    assert "long_term_target_hit" in events[0].reasons
    assert "intraday_target_hit" in events[0].reasons
    assert "all_horizons_closed" in events[0].reasons


@pytest.mark.unit
async def test_non_material_analyses_keep_bounded_provenance_without_snapshot_events() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )

    latest_result: AnalysisResult | None = None
    for offset in range(1, 101):
        latest_result = analysis(
            AnalysisHorizon.SWING,
            price=str(100 + offset / 100),
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            as_of=NOW + timedelta(minutes=offset),
            analysis_id=UUID(f"0195f3a5-9000-7000-8000-{offset:012d}"),
        )
        assert (
            await manager.ingest_analysis(
                latest_result,
                now=latest_result.as_of,
            )
            == ()
        )

    active = await store.load_active("AAPL")
    assert active is not None
    assert len(active.source_analysis_ids) <= 32
    assert active.source_analysis_ids[0] == UUID("0195f3a5-9000-7000-8000-000000000011")
    assert len(active.latest_analyses) == 1
    assert active.latest_analyses[0] == latest_result
    assert len(store.events) == 1
    revision = active.revision

    assert latest_result is not None
    assert await manager.ingest_analysis(latest_result, now=latest_result.as_of) == ()
    assert (await store.load_active("AAPL")).revision == revision  # type: ignore[union-attr]


@pytest.mark.unit
async def test_v2_core_signals_advance_l1_to_l4_without_producer_metadata() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV2(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    setup_id = "watch:0195f3a5-9000-7000-8000-000000000021"

    await manager.ingest_signal(
        entry_signal(
            EntrySignalFamily.CORE_ENTRY,
            signal_id="0195f3a5-9000-7000-8000-000000000201",
            setup_id=setup_id,
            created_at=NOW + timedelta(minutes=1),
            maturity=EntryMaturityLevel.L1,
        )
    )
    events = await manager.ingest_signal(
        entry_signal(
            EntrySignalFamily.CORE_ENTRY,
            signal_id="0195f3a5-9000-7000-8000-000000000202",
            setup_id=setup_id,
            created_at=NOW + timedelta(minutes=2),
            maturity=EntryMaturityLevel.L4,
        )
    )

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.OPEN
    assert active.peak_maturity is EntryMaturityLevel.L4
    assert active.primary_signal_family is EntrySignalFamily.CORE_ENTRY
    assert len(active.signal_references) == 1
    assert active.signal_references[0].maturity is EntryMaturityLevel.L4
    assert "engine_id" not in type(active.signal_references[0]).model_fields


@pytest.mark.unit
async def test_v2_recovery_gets_a_distinct_l4_checkpoint_and_outcome_setup() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV2(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )
    await manager.ingest_signal(
        entry_signal(
            EntrySignalFamily.CORE_ENTRY,
            signal_id="0195f3a5-9000-7000-8000-000000000211",
            setup_id="watch:0195f3a5-9000-7000-8000-000000000021",
            created_at=NOW + timedelta(minutes=1),
            maturity=EntryMaturityLevel.L4,
        )
    )

    await manager.ingest_signal(
        entry_signal(
            EntrySignalFamily.CORE_RECOVERY,
            signal_id="0195f3a5-9000-7000-8000-000000000212",
            setup_id="recovery:0195f3a5-9000-7000-8000-000000000001",
            created_at=NOW + timedelta(minutes=5),
            maturity=EntryMaturityLevel.L4,
        )
    )

    active = await store.load_active("AAPL")
    assert active is not None
    l4 = [item for item in active.checkpoints if item.level is EntryMaturityLevel.L4]
    assert [(item.signal_family, item.setup_id) for item in l4] == [
        (
            EntrySignalFamily.CORE_ENTRY,
            "watch:0195f3a5-9000-7000-8000-000000000021",
        ),
        (
            EntrySignalFamily.CORE_RECOVERY,
            "recovery:0195f3a5-9000-7000-8000-000000000001",
        ),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "family",
    (
        EntrySignalFamily.PATREON_CAPS,
        EntrySignalFamily.LONG_PORTFOLIO,
        EntrySignalFamily.SIGNAL_FUSION,
        EntrySignalFamily.PORTFOLIO_FLOW,
        EntrySignalFamily.LEVERAGED_THESIS,
    ),
)
async def test_v2_registers_analytical_families_without_core_maturity(
    family: EntrySignalFamily,
) -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV2(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )

    events = await manager.ingest_signal(
        entry_signal(
            family,
            signal_id=f"0195f3a5-9000-7000-8000-{list(EntrySignalFamily).index(family) + 301:012d}",
            setup_id=f"{family.value.lower()}:aapl:2026-08-09",
            created_at=NOW + timedelta(minutes=1),
        )
    )

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.ARMED
    assert active.current_maturity is EntryMaturityLevel.ARMED
    reference = active.signal_references[-1]
    assert reference.family is family
    assert reference.maturity is None
    assert all(leg.status is EntryLegStatus.WATCHING for leg in active.legs)


@pytest.mark.unit
async def test_v2_creates_standalone_paper_opportunity_for_complete_analytical_signal() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV2(store=store, id_factory=lambda: OPPORTUNITY_ID)
    signal = entry_signal(
        EntrySignalFamily.PATREON_CAPS,
        signal_id="0195f3a5-9000-7000-8000-000000000401",
        setup_id="patreon:aapl:2026-08-09",
        created_at=NOW,
    )

    events = await manager.ingest_signal(signal)

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.OPEN
    assert active.original_watch_id is None
    assert active.primary_signal_family is EntrySignalFamily.PATREON_CAPS
    assert active.signal_references[0].maturity is None
    assert {leg.horizon for leg in active.legs} == set(signal.horizons)
    assert all(leg.status is EntryLegStatus.OPEN for leg in active.legs)
    assert all(leg.target == Decimal("110") for leg in active.legs)


@pytest.mark.unit
async def test_v2_requires_complete_levels_for_standalone_signal() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV2(store=store, id_factory=lambda: OPPORTUNITY_ID)

    events = await manager.ingest_signal(
        entry_signal(
            EntrySignalFamily.LONG_PORTFOLIO,
            signal_id="0195f3a5-9000-7000-8000-000000000402",
            setup_id="long-portfolio:aapl:2026-08-09",
            created_at=NOW,
            complete_levels=False,
        )
    )

    assert events == ()
    assert await store.load_active("AAPL") is None


@pytest.mark.unit
async def test_v2_deduplicates_signals_by_id_and_setup() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV2(store=store, id_factory=lambda: OPPORTUNITY_ID)
    first = entry_signal(
        EntrySignalFamily.SIGNAL_FUSION,
        signal_id="0195f3a5-9000-7000-8000-000000000403",
        setup_id="fusion:aapl:2026-08-09",
        created_at=NOW,
    )
    await manager.ingest_signal(first)
    active = await store.load_active("AAPL")
    assert active is not None
    revision = active.revision

    assert await manager.ingest_signal(first) == ()
    assert (
        await manager.ingest_signal(
            first.model_copy(
                update={
                    "signal_id": UUID("0195f3a5-9000-7000-8000-000000000404"),
                    "created_at": NOW + timedelta(minutes=1),
                }
            )
        )
        == ()
    )
    active = await store.load_active("AAPL")
    assert active is not None
    assert active.revision == revision
    assert len(active.signal_references) == 1


@pytest.mark.unit
async def test_v2_legacy_alert_compatibility_delegates_to_entry_signal() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngineV2(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )

    events = await manager.ingest_alert(
        alert(EntryMaturityLevel.L1, created_at=NOW + timedelta(minutes=1))
    )

    active = await store.load_active("AAPL")
    assert len(events) == 1
    assert active is not None
    assert active.status is EntryOpportunityStatus.CONFIRMING
    assert active.signal_references[0].family is EntrySignalFamily.CORE_ENTRY
    assert active.signal_references[0].maturity is EntryMaturityLevel.L1


@pytest.mark.unit
async def test_daily_bar_cannot_apply_pre_entry_high_low_to_a_paper_trade() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await _open(manager)

    events = await manager.ingest_bar(
        bar(
            timestamp=NOW + timedelta(hours=6),
            close="104",
            low="80",
            high="120",
        ).model_copy(update={"timeframe": BarTimeframe.DAY_1})
    )

    assert events == ()
    assert await store.load_active("AAPL") is not None


@pytest.mark.unit
async def test_long_invalidation_closes_every_leg_and_audits_gain_loss() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await _open(manager)

    events = await manager.ingest_analysis(
        analysis(
            AnalysisHorizon.LONG_TERM,
            price="95",
            verdict=AnalysisVerdict.AVOID,
            direction=PatternDirection.BEARISH,
            as_of=NOW + timedelta(minutes=30),
        ),
        now=NOW + timedelta(minutes=30),
    )

    assert await store.load_active("AAPL") is None
    closed = await store.load_latest("AAPL")
    assert closed is not None
    assert closed.status is EntryOpportunityStatus.CLOSED
    assert closed.close_reason is EntryCloseReason.ORIGINAL_THESIS_INVALIDATED
    assert all(item.status is EntryLegStatus.THESIS_BROKEN for item in closed.legs)
    assert all(item.gain_loss_percent is not None for item in closed.legs)
    assert events[-1].opportunity.status is EntryOpportunityStatus.CLOSED


@pytest.mark.unit
async def test_extended_hours_wick_cannot_invalidate_an_opportunity() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await _open(manager)

    events = await manager.ingest_bar(
        bar(
            timestamp=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
            close="95",
            low="80",
            high="105",
        )
    )

    assert events == ()
    assert await store.load_active("AAPL") is not None


@pytest.mark.unit
async def test_armed_invalidation_closes_checkpoint_without_creating_another_opportunity() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )

    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.INVALIDATED,
            watch_id="0195f3a5-9000-7000-8000-000000000099",
            price="90",
            occurred_at=NOW + timedelta(minutes=10),
            previous=EntryWatchStatus.ARMED,
            reasons=("long_structure_invalidated",),
        )
    )

    closed = await store.load_latest("AAPL")
    assert closed is not None and closed.status is EntryOpportunityStatus.CLOSED
    assert closed.checkpoints[0].gain_loss_percent == Decimal("-10.0000")
    assert closed.close_reason is EntryCloseReason.ORIGINAL_THESIS_INVALIDATED
    assert len(store.opportunities) == 1


@pytest.mark.unit
async def test_policy_ineligible_watch_cannot_close_an_existing_opportunity() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )

    events = await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.POLICY_INELIGIBLE,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="101",
            occurred_at=NOW + timedelta(minutes=10),
            previous=EntryWatchStatus.ARMED,
            reasons=("policy_ineligible", "frozen_score_below_minimum:41.75<50"),
        )
    )

    active = await store.load_active("AAPL")
    assert active is not None
    assert active.status is EntryOpportunityStatus.ARMED
    assert active.checkpoints[0].status.value == "OPEN"
    assert events == ()


@pytest.mark.unit
async def test_reconciler_closes_expired_or_removed_symbols_without_new_analysis() -> None:
    store = InMemoryEntryOpportunityStore()
    manager = EntryOpportunityEngine(store=store, id_factory=lambda: OPPORTUNITY_ID)
    await manager.ingest_transition(
        watch_transition(
            EntryWatchStatus.ARMED,
            watch_id="0195f3a5-9000-7000-8000-000000000021",
            price="100",
        )
    )

    events = await manager.reconcile(
        now=NOW + timedelta(days=57),
        active_symbols={"AAPL"},
    )

    closed = await store.load_latest("AAPL")
    assert closed is not None
    assert closed.close_reason is EntryCloseReason.EXPIRED
    assert events[-1].opportunity.status is EntryOpportunityStatus.CLOSED
