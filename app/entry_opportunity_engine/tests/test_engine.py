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
    EntryWatchStatus,
    EntryWatchTransition,
    LocalAlert,
    MarketBar,
    NamedValue,
    PatternDirection,
)
from app.entry_opportunity_engine import (
    EntryOpportunityEngine,
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
) -> EntryWatchTransition:
    return EntryWatchTransition(
        transition_id=UUID(
            {
                EntryWatchStatus.ARMED: "0195f3a5-9000-7000-8000-000000000081",
                EntryWatchStatus.IN_ZONE: "0195f3a5-9000-7000-8000-000000000082",
                EntryWatchStatus.TRIGGERED: "0195f3a5-9000-7000-8000-000000000083",
                EntryWatchStatus.INVALIDATED: "0195f3a5-9000-7000-8000-000000000084",
                EntryWatchStatus.EXPIRED: "0195f3a5-9000-7000-8000-000000000085",
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
        analysis_id=UUID("0195f3a5-9000-7000-8000-000000000071"),
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
        metrics=tuple(metrics),
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
    assert {item.horizon for item in active.legs} == {
        AnalysisHorizon.LONG_TERM,
        AnalysisHorizon.SWING,
        AnalysisHorizon.INTRADAY,
    }
    assert len(store.opportunities) == 1


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

    assert await manager.ingest_alert(
        alert(EntryMaturityLevel.L3, created_at=NOW + timedelta(minutes=3))
    ) == ()
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
