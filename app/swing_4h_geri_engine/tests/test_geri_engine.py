from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryMaturityLevel,
    GeriLevelKind,
    GeriMaturity,
    MarketBar,
    NamedValue,
    PatternDirection,
    TradeSide,
)
from app.swing_4h_geri_engine import (
    Swing4HGeriContext,
    Swing4HGeriEngine,
    Swing4HGeriEngineV11,
    Swing4HGeriEngineV12,
    Swing4HGeriEngineV13,
    Swing4HGeriEngineV14,
)

START = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


def bar(index: int, *, low: str, high: str, close: str, open_: str | None = None) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.HOUR_4,
        timestamp=START + timedelta(hours=4 * index),
        open=Decimal(open_) if open_ is not None else Decimal(close) - Decimal("1"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
        is_final=True,
    )


def level_three_bars(*, bounce: bool = False) -> tuple[MarketBar, ...]:
    values = [
        ("99", "103", "101"),
        ("97", "102", "99"),
        ("100", "106", "105"),
        ("103", "110", "109"),
        ("100", "108", "101"),
        ("95", "102", "96"),
        ("93", "101", "95"),
        ("94", "105", "103"),
        ("101", "112", "111"),
    ]
    if bounce:
        values.extend(
            [
                ("93", "96", "94"),
                ("94", "101", "100"),
            ]
        )
    return tuple(
        bar(index, low=low, high=high, close=close)
        for index, (low, high, close) in enumerate(values)
    )


def bearish_level_three_bars() -> tuple[MarketBar, ...]:
    values = [
        ("105", "108", "107"),
        ("107", "110", "108"),
        ("101", "106", "102"),
        ("94", "103", "95"),
        ("96", "104", "103"),
        ("108", "112", "111"),
        ("106", "110", "107"),
        ("99", "107", "100"),
        ("92", "101", "93"),
    ]
    return tuple(
        bar(index, low=low, high=high, close=close)
        for index, (low, high, close) in enumerate(values)
    )


def confirmation_bar(index: int, *, low: str, high: str, close: str, open_: str) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.MINUTE_15,
        timestamp=START + timedelta(hours=4 * 9, minutes=15 * index),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
        is_final=True,
    )


def daily_swing() -> AnalysisResult:
    return AnalysisResult(
        symbol="AAPL",
        horizon=AnalysisHorizon.SWING,
        as_of=level_three_bars(bounce=True)[-1].timestamp,
        engine_id="swing",
        engine_version="5.0.0",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("72"),
        confidence=Decimal("0.72"),
        reasons=("pullback",),
        metrics=(
            NamedValue(name="entry_zone_low", value=Decimal("92")),
            NamedValue(name="entry_zone_high", value=Decimal("96")),
        ),
        context_hash=f"sha256:{'b' * 64}",
    )


def test_engine_reconstructs_support_resistance_support_after_opposite_breaks() -> None:
    bars = level_three_bars()

    result = Swing4HGeriEngine().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=bars[-1].close)
    )

    assert [(level.sequence, level.kind, level.price) for level in result.levels] == [
        (1, GeriLevelKind.SUPPORT, Decimal("97")),
        (2, GeriLevelKind.RESISTANCE, Decimal("110")),
        (3, GeriLevelKind.SUPPORT, Decimal("93")),
    ]
    assert result.levels[0].broken_at == bars[5].timestamp
    assert result.levels[1].broken_at == bars[8].timestamp
    assert result.maturity is GeriMaturity.ARMED


def test_engine_marks_level_three_retest_then_fresh_bounce() -> None:
    engine = Swing4HGeriEngine()
    base = level_three_bars()
    in_zone = engine.analyze(
        Swing4HGeriContext(symbol="AAPL", bars=base, current_price=Decimal("93.2"))
    )
    bounced = engine.analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=level_three_bars(bounce=True),
            current_price=Decimal("100"),
        )
    )

    assert in_zone.maturity is GeriMaturity.IN_ZONE_4H
    assert bounced.maturity is GeriMaturity.L2_4H
    assert bounced.bounce_confirmed is True


def test_engine_uses_daily_swing_and_existing_opportunity_only_after_4h_bounce() -> None:
    bars = level_three_bars(bounce=True)
    engine = Swing4HGeriEngine()

    l3 = engine.analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=bars,
            current_price=bars[-1].close,
            daily_swing=daily_swing(),
        )
    )
    l4 = engine.analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=bars,
            current_price=bars[-1].close,
            daily_swing=daily_swing(),
            existing_maturity=EntryMaturityLevel.L3,
        )
    )

    assert l3.maturity is GeriMaturity.L3
    assert l4.maturity is GeriMaturity.L4


def test_breaking_active_support_creates_next_resistance_instead_of_killing_structure() -> None:
    bars = (
        *level_three_bars(bounce=True),
        bar(11, low="90", high="95", close="91"),
    )

    result = Swing4HGeriEngine().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=bars[-1].close)
    )

    assert result.levels[-1].sequence == 4
    assert result.levels[-1].kind is GeriLevelKind.RESISTANCE
    assert result.maturity is GeriMaturity.BUILDING
    assert result.zone_low is None


def test_v11_keeps_the_active_level_chain_when_the_history_window_moves() -> None:
    engine = Swing4HGeriEngineV11()
    history = level_three_bars()
    active = engine.analyze(
        Swing4HGeriContext(symbol="AAPL", bars=history, current_price=history[-1].close)
    )
    next_bar = bar(9, low="100", high="109", close="108")

    updated = engine.analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=(*history[1:], next_bar),
            current_price=next_bar.close,
            active_structure=active,
        )
    )

    assert updated.levels == active.levels
    assert updated.active_level_sequence == active.active_level_sequence
    assert updated.active_level_kind is active.active_level_kind
    assert updated.active_level_price == active.active_level_price


def test_v11_appends_one_level_only_after_a_completed_break() -> None:
    engine = Swing4HGeriEngineV11()
    history = level_three_bars()
    active = engine.analyze(
        Swing4HGeriContext(symbol="AAPL", bars=history, current_price=history[-1].close)
    )
    breaking_bar = bar(9, low="89", high="94", close="90")

    updated = engine.analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=(*history, breaking_bar),
            current_price=breaking_bar.close,
            active_structure=active,
        )
    )

    assert updated.levels[:-1] == (
        *active.levels[:-1],
        active.levels[-1].model_copy(update={"broken_at": breaking_bar.timestamp}),
    )
    assert updated.levels[-1].sequence == active.active_level_sequence + 1
    assert updated.levels[-1].kind is GeriLevelKind.RESISTANCE
    assert updated.levels[-1].price == Decimal("112")


def test_v12_reconstructs_the_mirrored_bearish_chain() -> None:
    bars = bearish_level_three_bars()

    result = Swing4HGeriEngineV12().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("103"))
    )

    assert [(level.sequence, level.kind, level.price) for level in result.levels] == [
        (1, GeriLevelKind.RESISTANCE, Decimal("110")),
        (2, GeriLevelKind.SUPPORT, Decimal("94")),
        (3, GeriLevelKind.RESISTANCE, Decimal("112")),
    ]
    assert result.standalone_swing is True
    assert result.trade_side is TradeSide.SHORT
    assert result.active_level_kind is GeriLevelKind.RESISTANCE
    assert result.invalidation > result.zone_high
    assert result.maturity is GeriMaturity.ARMED


def test_v12_keeps_the_bullish_chain_without_daily_or_opportunity_inputs() -> None:
    bars = level_three_bars()

    result = Swing4HGeriEngineV12().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("100"))
    )

    assert result.standalone_swing is True
    assert result.trade_side is TradeSide.LONG
    assert result.active_level_kind is GeriLevelKind.SUPPORT
    assert result.maturity is GeriMaturity.ARMED
    assert "manual_monitor_only" in result.reasons


def test_v12_preserves_the_selected_chain_when_the_history_window_moves() -> None:
    engine = Swing4HGeriEngineV12()
    history = level_three_bars()
    active = engine.analyze(
        Swing4HGeriContext(symbol="AAPL", bars=history, current_price=Decimal("100"))
    )
    next_bar = bar(9, low="99", high="109", close="105")

    updated = engine.analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=(*history[1:], next_bar),
            current_price=next_bar.close,
            active_structure=active,
        )
    )

    assert updated.trade_side is active.trade_side
    assert updated.levels == active.levels
    assert updated.active_level_sequence == active.active_level_sequence


def test_v12_promotes_a_bearish_fast_rejection_to_g2() -> None:
    bars = bearish_level_three_bars()
    confirmations = (
        confirmation_bar(0, low="109", high="112.2", close="111.4", open_="110"),
        confirmation_bar(1, low="107", high="111.8", close="108", open_="111"),
    )

    result = Swing4HGeriEngineV12().analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=bars,
            current_price=Decimal("108"),
            confirmation_bars=confirmations,
        )
    )

    assert result.maturity is GeriMaturity.L2_4H
    assert result.fast_confirmation is True
    assert "g2_fast_rejection_confirmed" in result.reasons


def test_v12_promotes_a_completed_bearish_4h_reaction_without_daily_alignment() -> None:
    base = bearish_level_three_bars()
    bars = (
        *base,
        bar(9, low="108", high="112", close="111", open_="109"),
        bar(10, low="101", high="110", close="102", open_="109"),
    )

    result = Swing4HGeriEngineV12().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("102"))
    )

    assert result.maturity is GeriMaturity.L3
    assert result.four_hour_confirmation is True
    assert result.daily_swing_aligned is False
    assert "g3_completed_4h_reaction" in result.reasons


def test_v12_marks_extension_and_failed_level_reclaim_for_manual_monitoring() -> None:
    bars = bearish_level_three_bars()
    engine = Swing4HGeriEngineV12()

    extended = engine.analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("80"))
    )
    reclaim = engine.analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("115"))
    )

    assert extended.maturity is GeriMaturity.EXTENDED
    assert reclaim.maturity is GeriMaturity.RECLAIM_REQUIRED
    assert "manual_monitor_only" in extended.reasons
    assert "manual_monitor_only" in reclaim.reasons


def test_v13_tracks_a_recent_countertrend_long_without_changing_short_structure() -> None:
    bars = (
        *bearish_level_three_bars(),
        bar(9, low="80", high="98", close="90"),
        bar(10, low="85", high="96", close="92"),
    )

    result = Swing4HGeriEngineV13().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("80.2"))
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert result.trade_side is TradeSide.SHORT
    assert result.active_level_kind is GeriLevelKind.RESISTANCE
    assert result.active_level_price == Decimal("112")
    assert metrics["countertrend_side"] == TradeSide.LONG
    assert metrics["countertrend_state"] == GeriMaturity.IN_ZONE_4H
    assert metrics["countertrend_level_price"] == Decimal("80.0000")
    assert metrics["countertrend_level_kind"] == GeriLevelKind.SUPPORT
    assert metrics["countertrend_target"] == Decimal("112.0000")
    assert metrics["countertrend_emits_opportunities"] is False
    assert metrics["countertrend_places_orders"] is False


def test_v13_waits_for_the_following_bar_before_confirming_tactical_pivot() -> None:
    bars = (*bearish_level_three_bars(), bar(9, low="80", high="98", close="90"))

    result = Swing4HGeriEngineV13().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("80.2"))
    )

    assert not any(item.name.startswith("countertrend_") for item in result.metrics)


def test_v13_mirrors_countertrend_short_above_a_structural_long() -> None:
    bars = (
        *level_three_bars(),
        bar(9, low="105", high="120", close="110"),
        bar(10, low="104", high="115", close="108"),
    )

    result = Swing4HGeriEngineV13().analyze(
        Swing4HGeriContext(symbol="AAPL", bars=bars, current_price=Decimal("119.8"))
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert result.trade_side is TradeSide.LONG
    assert result.active_level_kind is GeriLevelKind.SUPPORT
    assert result.active_level_price == Decimal("93")
    assert metrics["countertrend_side"] == TradeSide.SHORT
    assert metrics["countertrend_state"] == GeriMaturity.IN_ZONE_4H
    assert metrics["countertrend_level_price"] == Decimal("120.0000")
    assert metrics["countertrend_level_kind"] == GeriLevelKind.RESISTANCE
    assert metrics["countertrend_target"] == Decimal("93.0000")


def test_v12_remains_without_the_countertrend_lane() -> None:
    result = Swing4HGeriEngineV12().analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=bearish_level_three_bars(),
            current_price=Decimal("103"),
        )
    )

    assert not any(item.name.startswith("countertrend_") for item in result.metrics)


def test_v14_keeps_countertrend_in_zone_as_watch_until_reaction_confirms() -> None:
    bars = (
        *bearish_level_three_bars(),
        bar(9, low="80", high="98", close="90"),
        bar(10, low="85", high="96", close="92"),
    )
    as_of = bars[-1].timestamp + timedelta(hours=4)

    result = Swing4HGeriEngineV14().analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=bars,
            current_price=Decimal("80.2"),
            as_of=as_of,
            current_price_at=as_of,
        )
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["countertrend_location_in_zone"] is True
    assert metrics["countertrend_state"] is GeriMaturity.ARMED
    assert metrics["countertrend_fast_confirmation"] is False


def test_v14_rejects_confirmation_bars_from_the_future() -> None:
    bars = bearish_level_three_bars()
    as_of = bars[-1].timestamp + timedelta(minutes=15)
    future = confirmation_bar(8, low="109", high="112", close="111", open_="110")

    with pytest.raises(ValueError, match="later than as_of"):
        Swing4HGeriEngineV14().analyze(
            Swing4HGeriContext(
                symbol="AAPL",
                bars=bars,
                current_price=Decimal("103"),
                confirmation_bars=(future,),
                as_of=as_of,
                current_price_at=as_of,
            )
        )


def test_v14_promotes_countertrend_only_after_fast_reaction() -> None:
    bars = (
        *bearish_level_three_bars(),
        bar(9, low="80", high="98", close="90"),
        bar(10, low="85", high="96", close="92"),
    )
    confirmed_at = bars[-1].timestamp
    confirmations = (
        confirmation_bar(20, low="79.8", high="81", close="80.1", open_="80.8").model_copy(
            update={"timestamp": confirmed_at + timedelta(minutes=15)}
        ),
        confirmation_bar(21, low="80", high="82", close="81.5", open_="80.2").model_copy(
            update={"timestamp": confirmed_at + timedelta(minutes=30)}
        ),
    )
    as_of = confirmed_at + timedelta(minutes=45)

    result = Swing4HGeriEngineV14().analyze(
        Swing4HGeriContext(
            symbol="AAPL",
            bars=bars,
            current_price=Decimal("81.5"),
            confirmation_bars=confirmations,
            as_of=as_of,
            current_price_at=as_of,
        )
    )
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["countertrend_state"] is GeriMaturity.L2_4H
    assert metrics["countertrend_fast_confirmation"] is True
