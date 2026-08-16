from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
