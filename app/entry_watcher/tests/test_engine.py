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
    EntryWatcherV51,
    EntryWatcherV52,
    EntryWatcherV53,
    EntryWatcherV54,
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
@pytest.mark.parametrize(
    ("classification", "price", "score", "distance_atr"),
    (
        ("extended", "106", "80", "0.25"),
        ("watch_pullback", "106", "32.33", "0.25"),
        ("watch_pullback", "120", "80", "3"),
    ),
)
async def test_v52_rejects_noisy_initial_armed_candidates(
    classification: str,
    price: str,
    score: str,
    distance_atr: str,
) -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV52(store=store)
    result = analysis(
        AnalysisHorizon.LONG_TERM,
        classification=classification,
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        price=price,
        score=score,
        extra_metrics=(
            NamedValue(
                name="distance_to_buy_zone_atr",
                value=Decimal(distance_atr),
            ),
        ),
    )

    transition = await watcher.ingest(result, now=NOW)

    assert transition is None
    assert await store.load_active("AAPL") is None


@pytest.mark.unit
async def test_v52_arms_a_quality_pullback_near_the_zone() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV52(store=store)
    result = analysis(
        AnalysisHorizon.LONG_TERM,
        classification="watch_pullback",
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        price="108",
        score="60",
        extra_metrics=(
            NamedValue(name="distance_to_buy_zone_atr", value=Decimal("0.5")),
        ),
    )

    transition = await watcher.ingest(result, now=NOW)

    assert transition is not None
    assert transition.status is EntryWatchStatus.ARMED


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
async def test_v53_triggers_l4_at_first_mature_confirmation_price() -> None:
    store = InMemoryEntryWatchStore()
    seed = EntryWatcherV5(store=store)
    await seed.ingest(long_watch(price="120"), now=NOW)
    watcher = EntryWatcherV53(store=store)
    await watcher.ingest(long_watch(price="120"), now=NOW)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="120",
            as_of=NOW + timedelta(minutes=1),
            extra_metrics=(
                NamedValue(name="anchored_vwap_gate_passed", value=True),
                NamedValue(name="target_2r", value=Decimal("140")),
                NamedValue(name="invalidation", value=Decimal("115")),
            ),
        ),
        now=NOW + timedelta(minutes=1),
    )
    first_intraday = analysis(
        AnalysisHorizon.INTRADAY,
        classification="bullish_breakout",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        price="120.10",
        as_of=NOW + timedelta(minutes=2),
        setup="bullish_breakout",
        engine_version="4.0.0",
        extra_metrics=(
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="mature_confirmation_gate_passed", value=True),
            NamedValue(name="entry_efficiency_gate_passed", value=True),
            NamedValue(name="confirmation_quality", value="strong"),
            NamedValue(name="five_minute_higher_low", value=True),
            NamedValue(name="entry_trigger_level", value=Decimal("120")),
            NamedValue(name="atr14", value=Decimal("1")),
            NamedValue(name="invalidation_level", value=Decimal("115")),
        ),
    )

    triggered = await watcher.ingest(first_intraday, now=NOW + timedelta(minutes=2))

    assert triggered is not None
    assert triggered.status is EntryWatchStatus.TRIGGERED
    assert triggered.current_price == Decimal("120.10")
    assert "mature_intraday_entry_confirmed" in triggered.reasons
    assert "fresh_mature_intraday_reconfirmed" not in triggered.reasons


@pytest.mark.unit
async def test_v53_preserves_v52_initial_armed_policy() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV53(store=store)
    hpe_like = analysis(
        AnalysisHorizon.LONG_TERM,
        classification="extended",
        verdict=AnalysisVerdict.CAUTION,
        direction=PatternDirection.BULLISH,
        price="52.39",
        score="33.75",
        zone_low="31.8307",
        zone_high="34.5986",
        invalidation="27.5286",
        extra_metrics=(
            NamedValue(name="setup_score", value=Decimal("85")),
            NamedValue(name="trend_template_score", value=Decimal("100")),
            NamedValue(name="distance_to_buy_zone_atr", value=Decimal("6.1609")),
        ),
    ).model_copy(update={"symbol": "HPE"})

    transition = await watcher.ingest(hpe_like, now=NOW)
    assert transition is None
    assert await store.load_active("HPE") is None


@pytest.mark.unit
async def test_v54_opens_an_early_entry_before_full_maturity() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV54(store=store)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.LONG_TERM,
            classification="watch_pullback",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="108",
            score="60",
            extra_metrics=(
                NamedValue(name="distance_to_buy_zone_atr", value=Decimal("0.5")),
            ),
        ),
        now=NOW,
    )
    await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="107",
            as_of=NOW + timedelta(minutes=1),
            extra_metrics=(
                NamedValue(name="anchored_vwap_gate_passed", value=True),
                NamedValue(name="target_2r", value=Decimal("120")),
                NamedValue(name="invalidation", value=Decimal("103")),
                NamedValue(name="atr14", value=Decimal("4")),
            ),
        ),
        now=NOW + timedelta(minutes=1),
    )
    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="bullish_breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="107",
            as_of=NOW + timedelta(minutes=2),
            setup="bullish_breakout",
            extra_metrics=(
                NamedValue(name="confirmation_gate_passed", value=True),
                NamedValue(name="entry_efficiency_gate_passed", value=True),
                NamedValue(name="mature_confirmation_gate_passed", value=False),
                NamedValue(name="entry_trigger_level", value=Decimal("106.5")),
                NamedValue(name="atr14", value=Decimal("1")),
                NamedValue(name="invalidation_level", value=Decimal("105.5")),
            ),
        ),
        now=NOW + timedelta(minutes=2),
    )

    assert transition is not None
    assert transition.status is EntryWatchStatus.EARLY_ENTRY
    assert "early_entry_confirmed" in transition.reasons


@pytest.mark.unit
async def test_v54_tracks_an_extended_impulse_and_enters_its_pullback() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV54(store=store)
    await watcher.ingest(
        analysis(
            AnalysisHorizon.LONG_TERM,
            classification="watch_pullback",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="108",
            score="60",
            extra_metrics=(
                NamedValue(name="distance_to_buy_zone_atr", value=Decimal("0.5")),
            ),
        ),
        now=NOW,
    )
    extended = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="breakout",
            verdict=AnalysisVerdict.FAVORABLE,
            direction=PatternDirection.BULLISH,
            price="115",
            as_of=NOW + timedelta(minutes=1),
            extra_metrics=(
                NamedValue(name="anchored_vwap_gate_passed", value=True),
                NamedValue(name="target_2r", value=Decimal("125")),
                NamedValue(name="invalidation", value=Decimal("103")),
                NamedValue(name="atr14", value=Decimal("4")),
            ),
        ),
        now=NOW + timedelta(minutes=1),
    )
    await watcher.ingest(
        analysis(
            AnalysisHorizon.INTRADAY,
            classification="neutral",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.NEUTRAL,
            price="115",
            as_of=NOW + timedelta(minutes=2),
            extra_metrics=(NamedValue(name="atr14", value=Decimal("1")),),
        ),
        now=NOW + timedelta(minutes=2),
    )
    assert extended is not None
    assert extended.status is EntryWatchStatus.IMPULSE_EXTENDED

    pullback = analysis(
        AnalysisHorizon.INTRADAY,
        classification="pullback",
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.NEUTRAL,
        price="111",
        as_of=NOW + timedelta(minutes=3),
        extra_metrics=(NamedValue(name="atr14", value=Decimal("1")),),
    ).model_copy(
        update={"analysis_id": UUID("0195f3a5-9000-7000-8000-000000000015")}
    )
    assert await watcher.ingest(pullback, now=NOW + timedelta(minutes=3)) is None

    reclaim = analysis(
        AnalysisHorizon.INTRADAY,
        classification="bullish_reclaim",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        price="112",
        as_of=NOW + timedelta(minutes=4),
        extra_metrics=(
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="entry_efficiency_gate_passed", value=True),
            NamedValue(name="five_minute_higher_low", value=True),
            NamedValue(name="entry_trigger_level", value=Decimal("111.5")),
            NamedValue(name="atr14", value=Decimal("1")),
            NamedValue(name="invalidation_level", value=Decimal("110.75")),
        ),
    ).model_copy(
        update={"analysis_id": UUID("0195f3a5-9000-7000-8000-000000000016")}
    )
    triggered = await watcher.ingest(reclaim, now=NOW + timedelta(minutes=4))

    assert triggered is not None
    assert triggered.status is EntryWatchStatus.EARLY_ENTRY
    assert "impulse_pullback_reclaimed" in triggered.reasons


@pytest.mark.unit
async def test_v54_closes_a_legacy_watch_that_fails_modern_arm_quality() -> None:
    store = InMemoryEntryWatchStore()
    legacy = EntryWatcherV2(store=store)
    armed = await legacy.ingest(
        analysis(
            AnalysisHorizon.LONG_TERM,
            classification="watch_pullback",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="107.27",
            score="41.75",
            zone_low="84.5092",
            zone_high="97.4253",
            invalidation="79.5109",
            extra_metrics=(
                NamedValue(
                    name="distance_to_buy_zone_atr",
                    value=Decimal("2.9979"),
                ),
            ),
        ),
        now=NOW,
    )
    assert armed is not None
    assert armed.status is EntryWatchStatus.ARMED

    watcher = EntryWatcherV54(store=store)
    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="pullback",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="111.27",
            as_of=NOW + timedelta(minutes=1),
            extra_metrics=(
                NamedValue(name="anchored_vwap_gate_passed", value=True),
                NamedValue(name="atr14", value=Decimal("2.8728")),
            ),
        ),
        now=NOW + timedelta(minutes=1),
    )

    active = await store.load_active("AAPL")
    assert transition is not None
    assert transition.status is EntryWatchStatus.POLICY_INELIGIBLE
    assert "policy_ineligible" in transition.reasons
    assert active is None
    latest = await store.load_latest("AAPL")
    assert latest is not None
    assert latest.status is EntryWatchStatus.POLICY_INELIGIBLE


@pytest.mark.unit
async def test_v54_apa_regression_recovers_the_post_impulse_pullback() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV54(store=store)
    long_result = analysis(
        AnalysisHorizon.LONG_TERM,
        classification="watch_pullback",
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.BULLISH,
        price="37.63",
        score="60",
        zone_low="30.8566",
        zone_high="36.9338",
        invalidation="29.5099",
        extra_metrics=(
            NamedValue(name="distance_to_buy_zone_atr", value=Decimal("0.5")),
        ),
    ).model_copy(update={"symbol": "APA"})
    await watcher.ingest(long_result, now=NOW)
    swing = analysis(
        AnalysisHorizon.SWING,
        classification="breakout",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        price="41.24",
        as_of=NOW + timedelta(minutes=1),
        extra_metrics=(
            NamedValue(name="anchored_vwap_gate_passed", value=True),
            NamedValue(name="target_2r", value=Decimal("51.996")),
            NamedValue(name="invalidation", value=Decimal("34.017")),
            NamedValue(name="atr14", value=Decimal("1.5875")),
        ),
    ).model_copy(update={"symbol": "APA"})
    extended = await watcher.ingest(swing, now=NOW + timedelta(minutes=1))
    assert extended is not None
    assert extended.status is EntryWatchStatus.IMPULSE_EXTENDED

    pullback = analysis(
        AnalysisHorizon.INTRADAY,
        classification="pullback",
        verdict=AnalysisVerdict.WATCH,
        direction=PatternDirection.NEUTRAL,
        price="39.08",
        as_of=NOW + timedelta(minutes=2),
        extra_metrics=(NamedValue(name="atr14", value=Decimal("0.4")),),
    ).model_copy(
        update={
            "symbol": "APA",
            "analysis_id": UUID("0195f3a5-9000-7000-8000-000000000017"),
        }
    )
    await watcher.ingest(pullback, now=NOW + timedelta(minutes=2))
    reclaim = analysis(
        AnalysisHorizon.INTRADAY,
        classification="bullish_reclaim",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        price="39.55",
        as_of=NOW + timedelta(minutes=3),
        extra_metrics=(
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="entry_efficiency_gate_passed", value=True),
            NamedValue(name="five_minute_higher_low", value=True),
            NamedValue(name="entry_trigger_level", value=Decimal("39.40")),
            NamedValue(name="atr14", value=Decimal("0.4")),
            NamedValue(name="invalidation_level", value=Decimal("38.98")),
        ),
    ).model_copy(
        update={
            "symbol": "APA",
            "analysis_id": UUID("0195f3a5-9000-7000-8000-000000000018"),
        }
    )
    entry = await watcher.ingest(reclaim, now=NOW + timedelta(minutes=3))

    assert entry is not None
    assert entry.status is EntryWatchStatus.EARLY_ENTRY
    assert entry.current_price == Decimal("39.55")
    assert entry.entry_invalidation == Decimal("38.9800")
    assert entry.entry_target == Decimal("41.2400")


@pytest.mark.unit
async def test_v51_restart_restores_latest_evidence_and_pending_reconfirmation() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV51(store=store)
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
    first = analysis(
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
    assert await watcher.ingest(first, now=NOW + timedelta(minutes=2)) is None

    restarted = EntryWatcherV51(store=store)
    second = first.model_copy(
        update={
            "analysis_id": UUID("0195f3a5-9000-7000-8000-000000000097"),
            "as_of": NOW + timedelta(minutes=5),
        }
    )

    triggered = await restarted.ingest(second, now=NOW + timedelta(minutes=5))

    assert triggered is not None
    assert triggered.status is EntryWatchStatus.TRIGGERED
    assert "fresh_mature_intraday_reconfirmed" in triggered.reasons


@pytest.mark.unit
async def test_v51_zone_exit_buffer_prevents_upper_boundary_chatter() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV51(
        store=store,
        zone_exit_buffer_percent=Decimal("0.25"),
    )
    await watcher.ingest(long_watch(price="103"), now=NOW)

    inside_buffer = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="105.10",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )
    active = await store.load_active("AAPL")

    assert inside_buffer is None
    assert active is not None and active.status is EntryWatchStatus.IN_ZONE

    outside_buffer = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="105.30",
            as_of=NOW + timedelta(minutes=2),
        ),
        now=NOW + timedelta(minutes=2),
    )

    assert outside_buffer is not None
    assert outside_buffer.status is EntryWatchStatus.ARMED


@pytest.mark.unit
async def test_v5_keeps_original_zero_buffer_zone_exit_for_rollback() -> None:
    store = InMemoryEntryWatchStore()
    watcher = EntryWatcherV5(store=store)
    await watcher.ingest(long_watch(price="103"), now=NOW)

    transition = await watcher.ingest(
        analysis(
            AnalysisHorizon.SWING,
            classification="setup",
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            price="105.10",
            as_of=NOW + timedelta(minutes=1),
        ),
        now=NOW + timedelta(minutes=1),
    )

    assert transition is not None
    assert transition.status is EntryWatchStatus.ARMED


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
            engine_version="9.1.0",
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
            engine_version="10.2.0",
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
            engine_version="10.2.0",
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
