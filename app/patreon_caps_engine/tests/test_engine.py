from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    MacroRegime,
    MarketBar,
    NamedValue,
    PatreonCapsState,
    PatternDirection,
    new_uuid7,
)
from app.patreon_caps_engine import PatreonCapsContext, PatreonCapsEngine, default_policy
from app.patreon_caps_engine.engine import _base_structure_confirmed
from app.patreon_caps_engine.models import PatreonCapsWatch, SupportZone

NOW = datetime(2026, 8, 1, 15, 30, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def _bars(timeframe: BarTimeframe, count: int, *, start: str, step: str) -> tuple[MarketBar, ...]:
    value = Decimal(start)
    delta = Decimal(step)
    spacing = timedelta(days=7 if timeframe is BarTimeframe.WEEK_1 else 1)
    result: list[MarketBar] = []
    for index in range(count):
        close = value + delta * index
        result.append(
            MarketBar(
                symbol="NVO",
                timeframe=timeframe,
                timestamp=NOW - spacing * (count - index),
                open=close - Decimal("0.10"),
                high=close + Decimal("0.70"),
                low=close - Decimal("0.70"),
                close=close,
                volume=Decimal("1000") * (Decimal("2") if index == count - 1 else 1),
                vwap=close,
                source="fixture",
                feed="test",
            )
        )
    return tuple(result)


def _analysis(horizon: AnalysisHorizon) -> AnalysisResult:
    metrics: tuple[NamedValue, ...] = ()
    if horizon is AnalysisHorizon.SWING:
        metrics = (
            NamedValue(name="anchored_vwap_gate_passed", value=True),
            NamedValue(name="structure_broken_confirmed", value=False),
            NamedValue(name="pivot_low_avwap", value=Decimal("49.5")),
            NamedValue(name="breakout_avwap", value=Decimal("50")),
        )
    if horizon is AnalysisHorizon.INTRADAY:
        metrics = (
            NamedValue(name="confirmation_gate_passed", value=True),
            NamedValue(name="setup", value="bullish_vwap_reclaim"),
        )
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="3.0.0" if horizon is not AnalysisHorizon.LONG_TERM else "2.0.0",
        symbol="NVO",
        horizon=horizon,
        as_of=NOW - timedelta(minutes=5),
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("85"),
        confidence=Decimal("0.85"),
        reasons=("fixture",),
        metrics=metrics,
        context_hash=HASH,
    )


def test_engine_arms_zone_then_confirms_support_without_invalidating_on_wick() -> None:
    policy = default_policy().model_copy(
        update={"minimum_confluence_score": Decimal("20")}
    )
    engine = PatreonCapsEngine(policy)
    daily = _bars(BarTimeframe.DAY_1, 260, start="40", step="0.04")
    weekly = _bars(BarTimeframe.WEEK_1, 220, start="35", step="0.07")
    intraday = _bars(BarTimeframe.MINUTE_15, 160, start="49", step="0.01")
    context = PatreonCapsContext(
        symbol="NVO",
        as_of=NOW,
        daily_bars=daily,
        weekly_bars=weekly,
        intraday_bars=intraday,
        analyses=tuple(_analysis(item) for item in AnalysisHorizon if item in {
            AnalysisHorizon.LONG_TERM, AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY
        }),
        macro_regime=MacroRegime.RISK_ON,
        portfolio_capital_usd=Decimal("103000"),
        target_weight_percent=Decimal("4.31"),
        held_quantity=Decimal("0"),
    )

    armed = engine.evaluate(context, now=NOW)
    assert armed.transition is not None
    assert armed.transition.state is PatreonCapsState.WATCH_ZONE

    tested = engine.evaluate(context, now=NOW + timedelta(minutes=15))
    assert tested.assessment.state in {
        PatreonCapsState.WATCH_ZONE,
        PatreonCapsState.SUPPORT_TEST,
    }
    assert tested.assessment.invalidation < min(bar.close for bar in intraday)


def _state_fixture(
    *, state: PatreonCapsState, stage: int = 0
) -> tuple[
    PatreonCapsEngine,
    PatreonCapsContext,
    PatreonCapsWatch,
    SupportZone,
    dict[AnalysisHorizon, AnalysisResult],
]:
    policy = default_policy()
    engine = PatreonCapsEngine(policy)
    daily = _bars(BarTimeframe.DAY_1, 260, start="40", step="0.04")
    weekly = _bars(BarTimeframe.WEEK_1, 220, start="35", step="0.07")
    intraday = _bars(BarTimeframe.MINUTE_15, 160, start="49", step="0.01")
    analyses = {
        horizon: _analysis(horizon)
        for horizon in (
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        )
    }
    context = PatreonCapsContext(
        symbol="NVO",
        as_of=NOW,
        daily_bars=daily,
        weekly_bars=weekly,
        intraday_bars=intraday,
        analyses=tuple(analyses.values()),
        macro_regime=MacroRegime.RISK_ON,
        portfolio_capital_usd=Decimal("103000"),
        target_weight_percent=Decimal("4.31"),
    )
    watch = PatreonCapsWatch(
        watch_id=new_uuid7(),
        symbol="NVO",
        rule_version="1.0.0",
        state=state,
        armed_at=NOW - timedelta(days=10),
        updated_at=NOW - timedelta(minutes=15),
        expires_at=NOW + timedelta(days=46),
        zone_low=Decimal("49.5"),
        zone_center=Decimal("50"),
        zone_high=Decimal("50.5"),
        invalidation=Decimal("45"),
        highest_price=Decimal("52.5") if stage else Decimal("50.59"),
        tranche_stage=stage,
        support_sources=("pivot_daily", "weekly_sma50", "pivot_daily_avwap"),
        source_analysis_ids=tuple(item.analysis_id for item in analyses.values()),
    )
    zone = SupportZone(
        low=watch.zone_low,
        center=watch.zone_center,
        high=watch.zone_high,
        invalidation=watch.invalidation,
        atr14=Decimal("2"),
        score=Decimal("90"),
        sources=watch.support_sources,
        defense_dates=(),
    )
    return engine, context, watch, zone, analyses


def test_state_machine_confirms_v_only_after_same_session_support_touch() -> None:
    engine, context, watch, zone, analyses = _state_fixture(
        state=PatreonCapsState.SUPPORT_TEST
    )

    next_state = engine._next_state(  # pyright: ignore[reportPrivateUsage]
        context=context,
        watch=watch,
        zone=zone,
        current_price=context.intraday_bars[-1].close,
        confirmation_score=Decimal("80"),
        alignment_score=Decimal("100"),
        alignment_blocked=False,
        lesson_gate_passed=True,
        patreon_score=Decimal("90"),
        macro_threshold=Decimal("75"),
        analyses=analyses,
        now=NOW,
    )

    assert next_state == (PatreonCapsState.CONFIRMED_V, "confirmed_v", "V", 1)


def test_state_machine_emits_impulse_retest_and_ignores_an_intraday_wick() -> None:
    engine, context, watch, zone, analyses = _state_fixture(
        state=PatreonCapsState.CONFIRMED_V, stage=1
    )
    intraday = (
        *context.intraday_bars[:-1],
        context.intraday_bars[-1].model_copy(update={"low": Decimal("44")}),
    )
    context = context.model_copy(update={"intraday_bars": intraday})

    next_state = engine._next_state(  # pyright: ignore[reportPrivateUsage]
        context=context,
        watch=watch,
        zone=zone,
        current_price=context.intraday_bars[-1].close,
        confirmation_score=Decimal("80"),
        alignment_score=Decimal("100"),
        alignment_blocked=False,
        lesson_gate_passed=True,
        patreon_score=Decimal("90"),
        macro_threshold=Decimal("75"),
        analyses=analyses,
        now=NOW,
    )

    assert next_state[0] is PatreonCapsState.IMPULSE_RETEST
    assert next_state[3] == 2


def test_daily_close_below_invalidation_invalidates() -> None:
    engine, context, watch, zone, analyses = _state_fixture(
        state=PatreonCapsState.SUPPORT_TEST
    )
    daily = (
        *context.daily_bars[:-1],
        context.daily_bars[-1].model_copy(update={"close": Decimal("44")}),
    )
    context = context.model_copy(update={"daily_bars": daily})

    next_state = engine._next_state(  # pyright: ignore[reportPrivateUsage]
        context=context,
        watch=watch,
        zone=zone,
        current_price=context.intraday_bars[-1].close,
        confirmation_score=Decimal("80"),
        alignment_score=Decimal("100"),
        alignment_blocked=False,
        lesson_gate_passed=True,
        patreon_score=Decimal("90"),
        macro_threshold=Decimal("75"),
        analyses=analyses,
        now=NOW,
    )

    assert next_state[0] is PatreonCapsState.INVALIDATED


def test_lesson_trend_gate_blocks_buy_but_keeps_support_watch_active() -> None:
    engine, context, watch, zone, analyses = _state_fixture(
        state=PatreonCapsState.SUPPORT_TEST
    )

    next_state = engine._next_state(  # pyright: ignore[reportPrivateUsage]
        context=context,
        watch=watch,
        zone=zone,
        current_price=context.intraday_bars[-1].close,
        confirmation_score=Decimal("90"),
        alignment_score=Decimal("100"),
        alignment_blocked=False,
        lesson_gate_passed=False,
        patreon_score=Decimal("95"),
        macro_threshold=Decimal("75"),
        analyses=analyses,
        now=NOW,
    )

    assert next_state[0] is PatreonCapsState.SUPPORT_TEST
    assert next_state[3] == 0


def test_base_structure_rejects_false_breakout_and_accepts_two_defenses() -> None:
    _, context, watch, zone, _ = _state_fixture(state=PatreonCapsState.SUPPORT_TEST)
    bars = [
        bar.model_copy(
            update={"low": Decimal("55"), "close": Decimal("56"), "high": Decimal("57")}
        )
        for bar in context.daily_bars[-5:]
    ]
    bars[1] = bars[1].model_copy(
        update={"low": Decimal("49.8"), "close": Decimal("50.2"), "high": Decimal("51")}
    )
    bars[2] = bars[2].model_copy(
        update={"low": Decimal("49.9"), "close": Decimal("50.3"), "high": Decimal("51")}
    )
    bars[3] = bars[3].model_copy(
        update={"low": Decimal("52"), "close": Decimal("50.5"), "high": Decimal("52.5")}
    )
    bars[4] = bars[4].model_copy(
        update={"low": Decimal("52"), "close": Decimal("50.7"), "high": Decimal("52.5")}
    )
    false_breakout = tuple(bars)
    bars[4] = bars[4].model_copy(update={"close": Decimal("52"), "high": Decimal("52.2")})

    assert _base_structure_confirmed(false_breakout, watch, zone) is False
    assert _base_structure_confirmed(tuple(bars), watch, zone) is True
