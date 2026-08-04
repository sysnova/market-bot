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
    extra_metrics: tuple[NamedValue, ...] = (),
) -> AnalysisResult:
    metrics = [
        NamedValue(name="classification", value=classification),
        NamedValue(name="reference_price", value=Decimal(price)),
    ]
    if horizon is AnalysisHorizon.LONG_TERM:
        metrics.extend(
            (
                NamedValue(name="buy_zone_low", value=Decimal("100")),
                NamedValue(name="buy_zone_high", value=Decimal("105")),
                NamedValue(name="invalidation", value=Decimal("92")),
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
