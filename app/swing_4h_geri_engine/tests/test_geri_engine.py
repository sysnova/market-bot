from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
)
from app.swing_4h_geri_engine import Swing4HGeriContext, Swing4HGeriEngine

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
