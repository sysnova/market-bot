from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryMaturityLevel,
    MarketBar,
    NamedValue,
    PatternDirection,
    SwingChannelMaturity,
)
from app.swing_channel_4h_engine import (
    SwingChannel4HContext,
    SwingChannel4HEngine,
    SwingChannel4HEngineV11,
    SwingChannel4HEngineV12,
    SwingChannel4HEngineV13,
)

START = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)


def bar(index: int, *, low: str, high: str, close: str, open_: str | None = None) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=BarTimeframe.HOUR_4,
        timestamp=START + timedelta(hours=4 * index),
        open=Decimal(open_) if open_ is not None else Decimal(close) - Decimal("0.5"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
        is_final=True,
    )


def channel_bars(*, bounce: bool = False) -> tuple[MarketBar, ...]:
    values = [
        ("101", "104", "103"),
        ("98", "102", "101"),
        ("94", "100", "98"),
        ("97", "103", "102"),
        ("100", "107", "106"),
        ("98", "104", "102"),
        ("101", "108", "107"),
        ("103", "111", "110"),
        ("102", "108", "106"),
        ("103.8", "106", "104.5"),
    ]
    if bounce:
        values.append(("104.2", "108", "107.5"))
    return tuple(
        bar(index, low=low, high=high, close=close)
        for index, (low, high, close) in enumerate(values)
    )


def structural_channel_bars() -> tuple[MarketBar, ...]:
    values = [
        ("101", "105", "103"),
        ("98", "103", "101"),
        ("94", "100", "98"),
        ("97", "104", "102"),
        ("101", "108", "106"),
        ("104", "112", "110"),
        ("103", "111", "108"),
        ("102", "109", "106"),
        ("101", "107", "105"),
        ("100", "105", "103"),
        ("98", "104", "102"),
        ("101", "110", "108"),
        ("104", "114", "112"),
        ("106", "116", "114"),
        ("105", "113", "111"),
        ("106", "115", "113"),
        ("107", "117", "115"),
        ("108", "118", "116"),
    ]
    return tuple(
        bar(index, low=low, high=high, close=close)
        for index, (low, high, close) in enumerate(values)
    )


def broken_historical_channel_bars() -> tuple[MarketBar, ...]:
    values = [
        ("101", "105", "103"),
        ("98", "103", "101"),
        ("94", "100", "98"),
        ("97", "104", "102"),
        ("100", "110", "108"),
        ("103", "112", "110"),
        ("102", "109", "107"),
        ("101", "108", "106"),
        ("100", "106", "104"),
        ("99", "105", "103"),
        ("98", "104", "102"),
        ("101", "108", "106"),
        ("104", "112", "110"),
        ("105", "113", "111"),
        ("90", "100", "91"),
    ]
    return tuple(
        bar(index, low=low, high=high, close=close)
        for index, (low, high, close) in enumerate(values)
    )


def restarted_channel_bars() -> tuple[MarketBar, ...]:
    values = [
        ("85", "89", "87"),
        ("83", "88", "86"),
        ("80", "86", "84"),
        ("82", "90", "88"),
        ("84", "95", "92"),
        ("85", "110", "107"),
        ("86", "98", "95"),
        ("87", "97", "94"),
        ("88", "96", "93"),
        ("89", "95", "92"),
        ("91", "96", "94"),
        ("92", "97", "95"),
        ("91", "96", "94"),
        ("92", "97", "95"),
        ("90", "96", "94"),
        ("93", "98", "96"),
        ("70", "80", "75"),
        ("73", "85", "82"),
        ("75", "90", "87"),
        ("77", "100", "96"),
        ("78", "95", "92"),
        ("79", "94", "91"),
        ("78", "92", "89"),
        ("76", "88", "85"),
        ("74", "86", "82"),
        ("77", "89", "86"),
        ("78", "91", "88"),
        ("79", "92", "89"),
        ("80", "93", "90"),
    ]
    return tuple(
        bar(index, low=low, high=high, close=close)
        for index, (low, high, close) in enumerate(values)
    )


def daily_swing(*, low: str = "103", high: str = "106") -> AnalysisResult:
    return AnalysisResult(
        symbol="AAPL",
        horizon=AnalysisHorizon.SWING,
        as_of=channel_bars(bounce=True)[-1].timestamp,
        engine_id="swing",
        engine_version="5.0.0",
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("72"),
        confidence=Decimal("0.72"),
        reasons=("pullback",),
        metrics=(
            NamedValue(name="entry_zone_low", value=Decimal(low)),
            NamedValue(name="entry_zone_high", value=Decimal(high)),
        ),
        context_hash="sha256:" + "b" * 64,
    )


def test_engine_arms_an_ascending_three_pivot_channel() -> None:
    bars = channel_bars()
    result = SwingChannel4HEngine().analyze(
        SwingChannel4HContext(symbol="AAPL", bars=bars, current_price=Decimal("108"))
    )

    assert result.maturity is SwingChannelMaturity.ARMED
    assert result.pivot_a_price == Decimal("94")
    assert result.pivot_b_price == Decimal("98")
    assert result.slope_per_bar > 0
    assert result.support < result.middle < result.resistance


def test_engine_marks_support_touch_as_in_zone_4h() -> None:
    bars = channel_bars()
    result = SwingChannel4HEngine().analyze(
        SwingChannel4HContext(symbol="AAPL", bars=bars, current_price=bars[-1].close)
    )

    assert result.maturity is SwingChannelMaturity.IN_ZONE_4H
    assert result.support_touch_count >= 1


def test_engine_promotes_higher_low_bounce_to_l2_4h() -> None:
    bars = channel_bars(bounce=True)
    result = SwingChannel4HEngine().analyze(
        SwingChannel4HContext(symbol="AAPL", bars=bars, current_price=bars[-1].close)
    )

    assert result.maturity is SwingChannelMaturity.L2_4H
    assert result.bounce_confirmed is True


def test_engine_does_not_treat_an_old_support_touch_as_a_fresh_bounce() -> None:
    bars = (
        *channel_bars(bounce=True),
        bar(11, low="110", high="113", close="112"),
        bar(12, low="111", high="115", close="114"),
    )

    result = SwingChannel4HEngine().analyze(
        SwingChannel4HContext(symbol="AAPL", bars=bars, current_price=bars[-1].close)
    )

    assert result.maturity is SwingChannelMaturity.ARMED
    assert result.bounce_confirmed is False


def test_engine_promotes_alignment_to_l3_and_existing_confirmation_to_l4() -> None:
    bars = channel_bars(bounce=True)
    engine = SwingChannel4HEngine()

    l3 = engine.analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=bars[-1].close,
            daily_swing=daily_swing(),
        )
    )
    l4 = engine.analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=bars[-1].close,
            daily_swing=daily_swing(),
            existing_maturity=EntryMaturityLevel.L3,
        )
    )

    assert l3.maturity is SwingChannelMaturity.L3
    assert l3.daily_swing_aligned is True
    assert l4.maturity is SwingChannelMaturity.L4
    assert l4.existing_maturity_aligned is True


def test_v11_keeps_the_armed_geometry_when_a_new_bar_arrives() -> None:
    bars = channel_bars()
    engine = SwingChannel4HEngineV11()
    armed = engine.analyze(
        SwingChannel4HContext(symbol="AAPL", bars=bars, current_price=Decimal("108"))
    )
    projected_support = armed.support + armed.slope_per_bar
    next_bar = bar(10, low="103", high="110", close="105")

    updated = engine.analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=(*bars, next_bar),
            current_price=projected_support,
            active_channel=armed,
        )
    )

    assert updated.pivot_a_at == armed.pivot_a_at
    assert updated.pivot_b_at == armed.pivot_b_at
    assert updated.pivot_c_at == armed.pivot_c_at
    assert updated.slope_per_bar == armed.slope_per_bar
    assert updated.width == armed.width
    assert updated.support == projected_support
    assert updated.maturity is SwingChannelMaturity.IN_ZONE_4H


def test_v11_does_not_reuse_an_invalidated_channel() -> None:
    bars = channel_bars()
    engine = SwingChannel4HEngineV11()
    armed = engine.analyze(
        SwingChannel4HContext(symbol="AAPL", bars=bars, current_price=Decimal("108"))
    )
    invalidated = armed.model_copy(
        update={"maturity": SwingChannelMaturity.INVALIDATED}
    )

    rebuilt = engine.analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=Decimal("108"),
            active_channel=invalidated,
        )
    )

    assert rebuilt.context_hash == armed.context_hash
    assert rebuilt.maturity is SwingChannelMaturity.ARMED


def test_v12_rejects_two_nearby_support_pivots_as_a_channel() -> None:
    bars = channel_bars()

    with pytest.raises(ValueError, match="structurally separated"):
        SwingChannel4HEngineV12().analyze(
            SwingChannel4HContext(
                symbol="AAPL",
                bars=bars,
                current_price=Decimal("108"),
            )
        )


def test_v12_anchors_the_channel_after_a_separated_impulse_and_retest() -> None:
    bars = structural_channel_bars()

    result = SwingChannel4HEngineV12().analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=Decimal("116"),
        )
    )

    assert result.pivot_a_at == bars[2].timestamp
    assert result.pivot_b_at == bars[10].timestamp
    assert result.pivot_b_at - result.pivot_a_at == timedelta(hours=32)
    assert result.slope_per_bar == Decimal("0.5000")


def test_v12_rejects_a_support_line_breached_between_a_and_b() -> None:
    bars = list(structural_channel_bars())
    bars[6] = bars[6].model_copy(update={"low": Decimal("85")})

    with pytest.raises(ValueError, match="structurally separated"):
        SwingChannel4HEngineV12().analyze(
            SwingChannel4HContext(
                symbol="AAPL",
                bars=tuple(bars),
                current_price=Decimal("116"),
            )
        )


def test_v12_rechecks_an_active_channel_created_by_an_older_engine() -> None:
    bars = channel_bars()
    legacy = SwingChannel4HEngineV11().analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=Decimal("108"),
        )
    )

    with pytest.raises(ValueError, match="structurally separated"):
        SwingChannel4HEngineV12().analyze(
            SwingChannel4HContext(
                symbol="AAPL",
                bars=bars,
                current_price=Decimal("108"),
                active_channel=legacy,
            )
        )


def test_v13_anchors_the_impulse_peak_between_separated_supports() -> None:
    bars = broken_historical_channel_bars()

    result = SwingChannel4HEngineV13().analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=bars[-1].close,
        )
    )

    assert result.pivot_a_at == bars[2].timestamp
    assert result.pivot_c_at == bars[5].timestamp
    assert result.pivot_b_at == bars[10].timestamp
    assert result.pivot_a_at < result.pivot_c_at < result.pivot_b_at
    assert result.slope_per_bar == Decimal("0.5000")


def test_v13_preserves_a_broken_channel_as_invalidated_geometry() -> None:
    bars = broken_historical_channel_bars()

    result = SwingChannel4HEngineV13().analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=bars[-1].close,
        )
    )

    assert result.maturity is SwingChannelMaturity.INVALIDATED
    assert result.support == Decimal("100.0000")
    assert result.middle > result.support
    assert result.resistance > result.middle
    assert "projected_support_invalidation_breached" in result.reasons


def test_v13_prefers_a_recent_mature_retest_after_an_old_channel_breaks() -> None:
    bars = restarted_channel_bars()

    result = SwingChannel4HEngineV13().analyze(
        SwingChannel4HContext(
            symbol="AAPL",
            bars=bars,
            current_price=bars[-1].close,
        )
    )

    assert result.pivot_a_at == bars[16].timestamp
    assert result.pivot_c_at == bars[19].timestamp
    assert result.pivot_b_at == bars[24].timestamp
