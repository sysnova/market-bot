from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    NamedValue,
    PatternDirection,
)
from app.entry_watcher import (
    EntryWatcher,
    EntryWatcherPolicy,
    EntryWatcherV2,
    EntryWatcherV3,
    EntryWatcherV4,
    EntryWatcherV5,
    InMemoryEntryWatchStore,
)

NOW = datetime(2026, 7, 26, 15, tzinfo=UTC)
HASH = "sha256:" + "a" * 64
WATCH_ID = UUID("0195f3a5-9000-7000-8000-000000000021")


def analysis(
    horizon: AnalysisHorizon,
    *,
    classification: str,
    verdict: AnalysisVerdict,
    direction: PatternDirection,
    price: str = "120",
    as_of: datetime = NOW,
    setup: str | None = None,
    engine_version: str = "1.0.0",
    score: str = "80",
    confidence: str = "0.8",
    zone_low: str = "100",
    zone_high: str = "105",
    invalidation: str = "92",
    extra_metrics: tuple[NamedValue, ...] = (),
) -> AnalysisResult:
    metrics = [
        NamedValue(name="classification", value=classification),
        NamedValue(name="reference_price", value=Decimal(price)),
    ]
    if horizon is AnalysisHorizon.LONG_TERM:
        metrics.extend(
            (
                NamedValue(name="buy_zone_low", value=Decimal(zone_low)),
                NamedValue(name="buy_zone_high", value=Decimal(zone_high)),
                NamedValue(name="invalidation", value=Decimal(invalidation)),
                NamedValue(name="support", value=Decimal("96")),
            )
        )
    if setup is not None:
        metrics.append(NamedValue(name="setup", value=setup))
    metrics.extend(extra_metrics)
    return AnalysisResult(
        analysis_id=UUID(
            {
                AnalysisHorizon.LONG_TERM: "0195f3a5-9000-7000-8000-000000000011",
                AnalysisHorizon.SWING: "0195f3a5-9000-7000-8000-000000000012",
                AnalysisHorizon.INTRADAY: "0195f3a5-9000-7000-8000-000000000013",
                AnalysisHorizon.DILUTION: "0195f3a5-9000-7000-8000-000000000014",
            }[horizon]
        ),
        engine_id=f"fixture-{horizon.value.lower()}",
        engine_version=engine_version,
        symbol="AAPL",
        horizon=horizon,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=Decimal(score),
        confidence=Decimal(confidence),
        reasons=("fixture",),
        metrics=tuple(metrics),
        context_hash=HASH,
    )


def long_watch(*, price: str = "120", as_of: datetime = NOW) -> AnalysisResult:
    return analysis(
        AnalysisHorizon.LONG_TERM,
        classification="extended",
        verdict=AnalysisVerdict.CAUTION,
        direction=PatternDirection.BULLISH,
        price=price,
        as_of=as_of,
    )


@pytest.mark.unit
async def test_extended_long_setup_arms_and_freezes_original_zone() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(
        store=store,
        policy=EntryWatcherPolicy(ttl=timedelta(weeks=8)),
        id_factory=lambda: WATCH_ID,
    )

    transition = await watcher.ingest(long_watch(), now=NOW)
    active = await store.load_active("AAPL")

    assert transition is not None
    assert transition.status is EntryWatchStatus.ARMED
    assert active is not None
    assert active.watch_id == WATCH_ID
    assert active.zone_low == Decimal("100")
    assert active.zone_high == Decimal("105")
    assert active.invalidation == Decimal("92")
    assert active.correction_target_percent == Decimal("12.5000")
    assert active.expires_at == NOW + timedelta(weeks=8)
    assert active.anchor_snapshot["metrics"]["support"] == "96"
    assert active.anchor_snapshot["watcher_engine_version"] == "1.0.0"


@pytest.mark.unit
async def test_zone_is_remembered_until_swing_and_intraday_confirm() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(store=store)
    await watcher.ingest(long_watch(), now=NOW)

    entered = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="103",
        ),
        now=NOW,
    )
    active = await store.load_active("AAPL")

    assert entered is not None and entered.status is EntryWatchStatus.IN_ZONE
    assert active is not None and active.zone_high == Decimal("105")

    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )
    triggered = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="vwap_reclaim",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=2),
        ),
        now=NOW + timedelta(minutes=2),
    )

    assert triggered is not None
    assert triggered.status is EntryWatchStatus.TRIGGERED
    assert "multi_horizon_entry_confirmed" in triggered.reasons
    assert "dilution_warning:unavailable" in triggered.reasons
    assert await store.load_active("AAPL") is None


@pytest.mark.unit
async def test_triggered_thesis_is_not_immediately_rearmed_by_same_long_result() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(store=store)
    await watcher.ingest(long_watch(), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )
    triggered = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="vwap_reclaim",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=2),
        ),
        now=NOW + timedelta(minutes=2),
    )

    rearmed = await watcher.ingest(
        long_watch(as_of=NOW + timedelta(minutes=3)),
        now=NOW + timedelta(minutes=3),
    )

    assert triggered is not None and triggered.status is EntryWatchStatus.TRIGGERED
    assert rearmed is None
    assert await store.load_active("AAPL") is None
    assert len(store.watches) == 1


@pytest.mark.unit
async def test_materially_changed_long_levels_can_arm_a_new_thesis() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(store=store)
    await watcher.ingest(long_watch(), now=NOW)
    invalidated = await watcher.ingest(
        analysis(
            AnalysisHorizon.LONG_TERM,
            classification="avoid",
            verdict=AnalysisVerdict.AVOID,
            direction=PatternDirection.BEARISH,
            price="101",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )

    rearmed = await watcher.ingest(
        analysis(
            AnalysisHorizon.LONG_TERM,
            classification="extended",
            verdict=AnalysisVerdict.CAUTION,
            direction=PatternDirection.BULLISH,
            price="120",
            zone_low="110",
            zone_high="115",
            invalidation="100",
            as_of=NOW + timedelta(minutes=2),
        ),
        now=NOW + timedelta(minutes=2),
    )

    assert invalidated is not None
    assert invalidated.status is EntryWatchStatus.INVALIDATED
    assert rearmed is not None and rearmed.status is EntryWatchStatus.ARMED
    active = await store.load_active("AAPL")
    assert active is not None and active.zone_low == Decimal("110")
    assert len(store.watches) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("invalidating", "reason"),
    (
        (
            analysis(
                AnalysisHorizon.LONG_TERM,
                classification="avoid",
                verdict=AnalysisVerdict.AVOID,
                direction=PatternDirection.BEARISH,
                price="101",
            ),
            "long_structure_invalidated",
        ),
        (
            analysis(
                AnalysisHorizon.INTRADAY,
                classification="breakdown",
                verdict=AnalysisVerdict.AVOID,
                direction=PatternDirection.BEARISH,
                price="91",
            ),
            "original_invalidation_breached",
        ),
    ),
)
async def test_only_explicit_invalidation_cancels_the_thesis(
    invalidating: AnalysisResult, reason: str
) -> None:
    watcher = EntryWatcher(store=InMemoryEntryWatchStore())
    await watcher.ingest(long_watch(), now=NOW)

    transition = await watcher.ingest(invalidating, now=NOW)

    assert transition is not None
    assert transition.status is EntryWatchStatus.INVALIDATED
    assert reason in transition.reasons


@pytest.mark.unit
async def test_watch_expires_without_reusing_stale_confirmation() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcher(store=store, policy=EntryWatcherPolicy(ttl=timedelta(days=1)))
    await watcher.ingest(long_watch(), now=NOW)

    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="neutral",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.NEUTRAL,
            as_of=NOW + timedelta(days=1),
        ),
        now=NOW + timedelta(days=1),
    )

    assert transition is not None
    assert transition.status is EntryWatchStatus.EXPIRED
    assert await store.load_active("AAPL") is None


@pytest.mark.unit
async def test_dilution_avoid_warns_but_does_not_block_entry_trigger() -> None:
    watcher = EntryWatcher(store=InMemoryEntryWatchStore())
    await watcher.ingest(long_watch(), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.DILUTION,
            classification="avoid",
            verdict=AnalysisVerdict.AVOID,
            direction=PatternDirection.BEARISH,
        ),
        now=NOW,
    )
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )

    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="vwap_reclaim",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=2),
        ),
        now=NOW + timedelta(minutes=2),
    )

    assert transition is not None
    assert transition.status is EntryWatchStatus.TRIGGERED
    assert "dilution_warning:avoid" in transition.reasons


@pytest.mark.unit
async def test_v2_ignores_intraday_invalidation_wick_and_requires_named_bullish_trigger() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV2(store=store)
    await watcher.ingest(long_watch(), now=NOW)

    wick = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="breakdown",
            verdict=AnalysisVerdict.AVOID,
            direction=PatternDirection.BEARISH,
            price="91",
            as_of=NOW + timedelta(seconds=1),
            setup="bearish_breakdown",
        ),
        now=NOW + timedelta(seconds=1),
    )

    assert wick is None
    assert await store.load_active("AAPL") is not None

    healthy_long_close = await watcher.ingest(
        long_watch(price="120", as_of=NOW),
        now=NOW + timedelta(seconds=2),
    )

    assert healthy_long_close is None
    assert await store.load_active("AAPL") is not None

    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )
    no_trigger = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="no_trigger",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=2),
            setup="no_trigger",
        ),
        now=NOW + timedelta(minutes=2),
    )
    triggered = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="vwap_reclaim",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=3),
            setup="bullish_vwap_reclaim",
        ),
        now=NOW + timedelta(minutes=3),
    )

    assert no_trigger is None
    assert triggered is not None
    assert triggered.status is EntryWatchStatus.TRIGGERED
    assert "regime_aware_entry_confirmed" in triggered.reasons


@pytest.mark.unit
async def test_v3_recent_zone_touch_can_trigger_on_a_moderate_opening_breakaway() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV3(store=store)
    await watcher.ingest(long_watch(), now=NOW)
    entered = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="103",
        ),
        now=NOW,
    )

    assert entered is not None and entered.status is EntryWatchStatus.IN_ZONE

    next_open = NOW + timedelta(hours=18)
    breakaway_watch = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="extended",
            verdict=AnalysisVerdict.CAUTION,
            direction=PatternDirection.BULLISH,
            price="107",
            as_of=next_open,
            engine_version="3.0.0",
            extra_metrics=(
                NamedValue(name="atr14", value=Decimal("4")),
                NamedValue(name="target_2r", value=Decimal("121")),
                NamedValue(name="invalidation", value=Decimal("101")),
                NamedValue(name="anchored_vwap_gate_passed", value=True),
            ),
        ),
        now=next_open,
    )

    assert breakaway_watch is not None
    assert breakaway_watch.status is EntryWatchStatus.ARMED
    assert "breakaway_continuation_pending" in breakaway_watch.reasons

    confirmed_at = next_open + timedelta(minutes=10)
    triggered = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="bullish_vwap_reclaim",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="107.20",
            as_of=confirmed_at,
            setup="bullish_vwap_reclaim",
            engine_version="3.0.0",
            extra_metrics=(
                NamedValue(name="confirmation_gate_passed", value=True),
                NamedValue(name="confirmation_quality", value="strong"),
                NamedValue(name="invalidation_level", value=Decimal("101")),
            ),
        ),
        now=confirmed_at,
    )

    assert triggered is not None
    assert triggered.status is EntryWatchStatus.TRIGGERED
    assert "breakaway_continuation_confirmed" in triggered.reasons
    assert any(reason.startswith("continuation_extension_percent:") for reason in triggered.reasons)
    assert any(reason.startswith("continuation_reward_risk:") for reason in triggered.reasons)
    assert await store.load_active("AAPL") is None


@pytest.mark.unit
async def test_v3_breakaway_beyond_the_chase_cap_rearms_and_waits_for_retest() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV3(store=store)
    await watcher.ingest(long_watch(), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="103",
        ),
        now=NOW,
    )

    next_open = NOW + timedelta(hours=18)
    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="extended",
            verdict=AnalysisVerdict.CAUTION,
            direction=PatternDirection.BULLISH,
            price="110",
            as_of=next_open,
            engine_version="3.0.0",
            extra_metrics=(
                NamedValue(name="atr14", value=Decimal("4")),
                NamedValue(name="target_2r", value=Decimal("130")),
                NamedValue(name="invalidation", value=Decimal("101")),
                NamedValue(name="anchored_vwap_gate_passed", value=True),
            ),
        ),
        now=next_open,
    )

    assert transition is not None
    assert transition.status is EntryWatchStatus.ARMED
    assert "left_target_zone_without_confirmation" in transition.reasons
    assert "continuation_chase_cap_exceeded" in transition.reasons


@pytest.mark.unit
async def test_v3_breakaway_does_not_trigger_when_live_reward_risk_falls_below_two() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV3(store=store)
    await watcher.ingest(long_watch(), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="103",
        ),
        now=NOW,
    )

    next_open = NOW + timedelta(hours=18)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="extended",
            verdict=AnalysisVerdict.CAUTION,
            direction=PatternDirection.BULLISH,
            price="107",
            as_of=next_open,
            engine_version="3.0.0",
            extra_metrics=(
                NamedValue(name="atr14", value=Decimal("4")),
                NamedValue(name="target_2r", value=Decimal("115")),
                NamedValue(name="invalidation", value=Decimal("101")),
                NamedValue(name="anchored_vwap_gate_passed", value=True),
            ),
        ),
        now=next_open,
    )
    confirmed_at = next_open + timedelta(minutes=10)
    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="bullish_vwap_reclaim",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="107.20",
            as_of=confirmed_at,
            setup="bullish_vwap_reclaim",
            engine_version="3.0.0",
            extra_metrics=(
                NamedValue(name="confirmation_gate_passed", value=True),
                NamedValue(name="confirmation_quality", value="strong"),
                NamedValue(name="invalidation_level", value=Decimal("101")),
            ),
        ),
        now=confirmed_at,
    )

    assert transition is None
    active = await store.load_active("AAPL")
    assert active is not None and active.status is EntryWatchStatus.ARMED


@pytest.mark.unit
async def test_v4_requires_a_fresh_second_mature_intraday_confirmation() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV4(store=store)
    await watcher.ingest(long_watch(price="103"), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=1),
            engine_version="3.0.0",
            extra_metrics=(NamedValue(name="anchored_vwap_gate_passed", value=True),),
        ),
        now=NOW + timedelta(minutes=1),
    )
    first = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="bullish_breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=2),
            setup="bullish_breakout",
            engine_version="4.0.0",
            extra_metrics=(
                NamedValue(name="confirmation_gate_passed", value=True),
                NamedValue(name="mature_confirmation_gate_passed", value=True),
                NamedValue(name="entry_efficiency_gate_passed", value=True),
                NamedValue(name="confirmation_quality", value="strong"),
                NamedValue(name="five_minute_higher_low", value=True),
            ),
        ),
        now=NOW + timedelta(minutes=2),
    )
    second_result = analysis(
        AnalysisHorizon.INTRADAY,
        classification="bullish_breakout",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        price="103.10",
        as_of=NOW + timedelta(minutes=5),
        setup="bullish_breakout",
        engine_version="4.0.0",
        extra_metrics=(
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="mature_confirmation_gate_passed", value=True),
            NamedValue(name="entry_efficiency_gate_passed", value=True),
            NamedValue(name="confirmation_quality", value="strong"),
            NamedValue(name="five_minute_higher_low", value=True),
        ),
    ).model_copy(update={"analysis_id": UUID("0195f3a5-9000-7000-8000-000000000099")})
    second = await watcher.ingest(second_result, now=NOW + timedelta(minutes=5))

    assert first is None
    assert second is not None
    assert second.status is EntryWatchStatus.TRIGGERED
    assert "fresh_mature_intraday_reconfirmed" in second.reasons


@pytest.mark.unit
async def test_v5_abnb_regression_triggers_without_touching_the_frozen_long_zone() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV5(store=store)
    abnb_long = analysis(
        AnalysisHorizon.LONG_TERM,
        classification="extended",
        verdict=AnalysisVerdict.CAUTION,
        direction=PatternDirection.BULLISH,
        price="149.355",
        zone_low="127.23",
        zone_high="143.922",
        invalidation="123.4131",
    ).model_copy(update={"symbol": "ABNB"})
    await watcher.ingest(abnb_long, now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="149.355",
            as_of=NOW + timedelta(minutes=1),
            engine_version="3.0.0",
            extra_metrics=(
                NamedValue(name="anchored_vwap_gate_passed", value=True),
                NamedValue(name="atr14", value=Decimal("6.0614")),
                NamedValue(name="target_2r", value=Decimal("165")),
                NamedValue(name="invalidation", value=Decimal("145")),
            ),
        ).model_copy(update={"symbol": "ABNB"}),
        now=NOW + timedelta(minutes=1),
    )

    def intraday_confirmation(*, analysis_id: UUID, price: str, minute: int) -> AnalysisResult:
        return analysis(
            AnalysisHorizon.INTRADAY,
            classification="bullish_breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price=price,
            as_of=NOW + timedelta(minutes=minute),
            setup="bullish_breakout",
            engine_version="4.0.0",
            extra_metrics=(
                NamedValue(name="confirmation_gate_passed", value=True),
                NamedValue(name="mature_confirmation_gate_passed", value=True),
                NamedValue(name="entry_efficiency_gate_passed", value=True),
                NamedValue(name="confirmation_quality", value="strong"),
                NamedValue(name="five_minute_higher_low", value=True),
                NamedValue(name="entry_trigger_level", value=Decimal("149")),
                NamedValue(name="atr14", value=Decimal("1")),
                NamedValue(name="invalidation_level", value=Decimal("145")),
            ),
        ).model_copy(update={"analysis_id": analysis_id, "symbol": "ABNB"})

    first = await watcher.ingest(
        intraday_confirmation(
            analysis_id=UUID("0195f3a5-9000-7000-8000-000000000091"),
            price="149.10",
            minute=2,
        ),
        now=NOW + timedelta(minutes=2),
    )
    second = await watcher.ingest(
        intraday_confirmation(
            analysis_id=UUID("0195f3a5-9000-7000-8000-000000000092"),
            price="149.20",
            minute=7,
        ),
        now=NOW + timedelta(minutes=7),
    )

    assert first is None
    assert second is not None and second.status is EntryWatchStatus.TRIGGERED
    assert "no_retest_higher_low_continuation_confirmed" in second.reasons
    assert "consistent_five_minute_higher_low" in second.reasons
    assert any(reason.startswith("continuation_reward_risk:") for reason in second.reasons)
    latest = await store.load_latest("ABNB")
    assert latest is not None and "zone_touched_at" not in latest.anchor_snapshot


@pytest.mark.unit
async def test_v5_no_zone_touch_does_not_chase_beyond_intraday_extension_cap() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV5(store=store)
    await watcher.ingest(long_watch(price="124"), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="124",
            as_of=NOW + timedelta(minutes=1),
            engine_version="3.0.0",
            extra_metrics=(
                NamedValue(name="anchored_vwap_gate_passed", value=True),
                NamedValue(name="target_2r", value=Decimal("150")),
                NamedValue(name="invalidation", value=Decimal("116")),
            ),
        ),
        now=NOW + timedelta(minutes=1),
    )
    for minute, analysis_id in (
        (2, UUID("0195f3a5-9000-7000-8000-000000000093")),
        (7, UUID("0195f3a5-9000-7000-8000-000000000094")),
    ):
        transition = await watcher.ingest(
            analysis(
                AnalysisHorizon.INTRADAY,
                classification="bullish_breakout",
                verdict=AnalysisVerdict.FAVORABLE,
                direction=PatternDirection.BULLISH,
                price="124",
                as_of=NOW + timedelta(minutes=minute),
                setup="bullish_breakout",
                engine_version="4.0.0",
                extra_metrics=(
                    NamedValue(name="confirmation_gate_passed", value=True),
                    NamedValue(name="mature_confirmation_gate_passed", value=True),
                    NamedValue(name="entry_efficiency_gate_passed", value=True),
                    NamedValue(name="confirmation_quality", value="strong"),
                    NamedValue(name="five_minute_higher_low", value=True),
                    NamedValue(name="entry_trigger_level", value=Decimal("120")),
                    NamedValue(name="atr14", value=Decimal("4")),
                    NamedValue(name="invalidation_level", value=Decimal("118")),
                ),
            ).model_copy(update={"analysis_id": analysis_id}),
            now=NOW + timedelta(minutes=minute),
        )

    assert transition is None
    active = await store.load_active("AAPL")
    assert active is not None and active.status is EntryWatchStatus.ARMED


@pytest.mark.unit
async def test_v4_does_not_rearm_a_different_zone_immediately_after_trigger() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV4(store=store)
    await watcher.ingest(long_watch(price="103"), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="103",
            as_of=NOW + timedelta(minutes=1),
            engine_version="3.0.0",
            extra_metrics=(NamedValue(name="anchored_vwap_gate_passed", value=True),),
        ),
        now=NOW + timedelta(minutes=1),
    )
    first_intraday = analysis(
        AnalysisHorizon.INTRADAY,
        classification="bullish_breakout",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        price="103",
        as_of=NOW + timedelta(minutes=2),
        setup="bullish_breakout",
        engine_version="4.0.0",
        extra_metrics=(
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="mature_confirmation_gate_passed", value=True),
            NamedValue(name="entry_efficiency_gate_passed", value=True),
            NamedValue(name="confirmation_quality", value="strong"),
            NamedValue(name="five_minute_higher_low", value=True),
        ),
    )
    await watcher.ingest(first_intraday, now=NOW + timedelta(minutes=2))
    second_intraday = first_intraday.model_copy(
        update={
            "analysis_id": UUID("0195f3a5-9000-7000-8000-000000000098"),
            "as_of": NOW + timedelta(minutes=5),
        }
    )
    triggered = await watcher.ingest(second_intraday, now=NOW + timedelta(minutes=5))
    rearmed = await watcher.ingest(
        analysis(
            AnalysisHorizon.LONG_TERM,
            classification="buy_zone",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="104",
            as_of=NOW + timedelta(minutes=6),
            zone_low="101",
            zone_high="106",
            invalidation="93",
        ),
        now=NOW + timedelta(minutes=6),
    )

    assert triggered is not None and triggered.status is EntryWatchStatus.TRIGGERED
    assert rearmed is None
    assert await store.load_active("AAPL") is None
