from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import BarTimeframe, MarketBar, SupportConfirmationType, SupportState
from app.support_confirmation_engine import (
    SupportConfirmationEngine,
    SupportContext,
    SupportZoneHint,
)

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def _bar(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1000",
) -> MarketBar:
    return MarketBar(
        symbol="TGT",
        timeframe=BarTimeframe.DAY_1,
        timestamp=NOW - timedelta(days=40 - index),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        source="fixture",
        feed="test",
    )


def _zone() -> SupportZoneHint:
    return SupportZoneHint(
        low=Decimal("99"),
        center=Decimal("100"),
        high=Decimal("101"),
        invalidation=Decimal("96"),
        score=Decimal("80"),
        sources=("pivot_daily", "daily_sma200", "fib_0618"),
    )


def _sweep_bars() -> tuple[MarketBar, ...]:
    values = (
        ("106", "108", "105", "107", "1000"),
        ("107", "109", "106", "108", "1000"),
        ("108", "110", "107", "109", "1000"),
        ("109", "111", "108", "110", "1000"),
        ("110", "111", "108", "109", "1000"),
        ("109", "110", "107", "108", "1000"),
        ("108", "109", "106", "107", "1000"),
        ("107", "108", "105", "106", "1000"),
        ("106", "107", "104", "105", "1000"),
        ("105", "106", "103", "104", "1000"),
        ("104", "105", "102", "103", "1000"),
        ("103", "104", "101", "102", "1000"),
        ("102", "104", "100", "103", "1100"),
        ("103", "106", "102", "105", "1100"),
        ("105", "107", "104", "106", "1000"),
        ("106", "107", "103", "104", "1000"),
        ("104", "105", "101", "102", "1000"),
        ("102", "104", "98", "103", "2600"),
        ("103", "105", "102", "104", "1600"),
        ("104", "106", "103", "105", "1400"),
    )
    return tuple(
        _bar(
            index,
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
        for index, (open_, high, low, close, volume) in enumerate(values)
    )


def test_sweep_reclaim_confirms_reaction_but_keeps_wave_b_risk() -> None:
    result = SupportConfirmationEngine().evaluate(
        SupportContext(symbol="TGT", daily_bars=_sweep_bars(), zone_hint=_zone())
    )

    assert result.state is SupportState.RECLAIMED
    assert result.confirmation_type is SupportConfirmationType.SWEEP_RECLAIM
    assert result.liquidity_sweep is True
    assert result.reaction_score >= Decimal("65")
    assert result.reversal_score < Decimal("60")
    assert result.b_wave_risk is True


def test_higher_high_and_higher_low_promote_structural_reversal() -> None:
    bars = (
        *_sweep_bars(),
        _bar(20, open_="105", high="114", low="104", close="113", volume="2200"),
        _bar(21, open_="113", high="114", low="105", close="107"),
        _bar(22, open_="107", high="110", low="106", close="109"),
        _bar(23, open_="109", high="116", low="108", close="115", volume="1900"),
    )

    result = SupportConfirmationEngine().evaluate(
        SupportContext(symbol="TGT", daily_bars=bars, zone_hint=_zone())
    )

    assert result.state is SupportState.STRUCTURE_CONFIRMED
    assert result.higher_high is True
    assert result.higher_low is True
    assert result.reversal_score >= Decimal("60")
    assert result.b_wave_risk is False


def test_short_consolidation_remains_base_building_without_breakout() -> None:
    bars = tuple(
        _bar(
            index,
            open_=str(102 + index % 2),
            high=str(104 - (index % 2)),
            low=str(99 + (index % 2)),
            close=str(102 + (index % 2)),
            volume=str(1400 - index * 20),
        )
        for index in range(18)
    )

    result = SupportConfirmationEngine().evaluate(
        SupportContext(symbol="TGT", daily_bars=bars, zone_hint=_zone())
    )

    assert result.state is SupportState.BASE_BUILDING
    assert result.confirmation_type is SupportConfirmationType.BASE_BREAKOUT
    assert result.reversal_score < Decimal("60")


def test_without_confluence_engine_reports_no_nearby_support() -> None:
    bars = tuple(
        _bar(
            index,
            open_=str(200 + index),
            high=str(202 + index),
            low=str(199 + index),
            close=str(201 + index),
        )
        for index in range(15)
    )

    result = SupportConfirmationEngine().evaluate(
        SupportContext(symbol="TGT", daily_bars=bars)
    )

    assert result.state is SupportState.NO_NEARBY_SUPPORT
    assert result.zone_low is None
    assert result.b_wave_risk is False
    assert "no_nearby_higher_timeframe_support" in result.reasons


def test_engine_discovers_higher_timeframe_support_confluence() -> None:
    daily = tuple(
        MarketBar(
            symbol="TGT",
            timeframe=BarTimeframe.DAY_1,
            timestamp=NOW - timedelta(days=80 - index),
            open=Decimal("100.1"),
            high=Decimal("102") if index == 40 else Decimal("101"),
            low=Decimal("99.9") if index in {10, 30, 50} else Decimal("100"),
            close=Decimal("100.2"),
            volume=Decimal("1000"),
            source="fixture",
            feed="test",
        )
        for index in range(60)
    )
    weekly = tuple(
        MarketBar(
            symbol="TGT",
            timeframe=BarTimeframe.WEEK_1,
            timestamp=NOW - timedelta(weeks=60 - index),
            open=Decimal("100.1"),
            high=Decimal("101"),
            low=Decimal("99.9") if index in {12, 32, 48} else Decimal("100"),
            close=Decimal("100.2"),
            volume=Decimal("5000"),
            source="fixture",
            feed="test",
        )
        for index in range(55)
    )

    result = SupportConfirmationEngine().evaluate(
        SupportContext(symbol="TGT", daily_bars=daily, weekly_bars=weekly)
    )

    assert result.state is not SupportState.NO_NEARBY_SUPPORT
    assert result.zone_low is not None
    assert len(result.support_sources) >= 2


def test_engine_preserves_origin_of_a_recent_violent_impulse() -> None:
    prices = (
        *("100" for _ in range(15)),
        "82",
        "85",
        "88",
        "92",
        "96",
        "100",
        "104",
        "108",
        "111",
        "113",
        "114",
        "115",
        "116",
        "117",
        "118",
    )
    bars = tuple(
        _bar(
            index,
            open_=price,
            high=str(Decimal(price) + Decimal("2")),
            low=("80" if index == 15 else str(Decimal(price) - Decimal("1"))),
            close=price,
        )
        for index, price in enumerate(prices)
    )

    result = SupportConfirmationEngine().evaluate(
        SupportContext(symbol="TGT", daily_bars=bars)
    )

    assert result.impulse_origin == Decimal("80")
    assert result.impulse_origin_at == bars[15].timestamp
    assert result.impulse_peak == Decimal("120")
    assert result.impulse_advance_percent == Decimal("50.0000")


def test_failed_reaction_keeps_explicit_b_wave_risk() -> None:
    engine = SupportConfirmationEngine()
    first = engine.evaluate(
        SupportContext(symbol="TGT", daily_bars=_sweep_bars(), zone_hint=_zone())
    )
    failed = (
        *_sweep_bars(),
        _bar(20, open_="102", high="103", low="98", close="99"),
    )

    result = engine.evaluate(
        SupportContext(
            symbol="TGT",
            daily_bars=failed,
            previous_assessment=first,
            zone_hint=_zone(),
        )
    )

    assert result.state is SupportState.B_WAVE_RISK
    assert result.b_wave_risk is True


def test_close_below_invalidation_breaks_the_support_thesis() -> None:
    bars = (
        *_sweep_bars(),
        _bar(20, open_="98", high="99", low="94", close="95"),
    )

    result = SupportConfirmationEngine().evaluate(
        SupportContext(symbol="TGT", daily_bars=bars, zone_hint=_zone())
    )

    assert result.state is SupportState.INVALIDATED


def test_support_input_models_reject_mixed_or_invalid_data() -> None:
    with pytest.raises(ValueError, match="out of order"):
        SupportZoneHint(
            low=Decimal("101"),
            center=Decimal("100"),
            high=Decimal("102"),
            invalidation=Decimal("96"),
            score=Decimal("50"),
            sources=("pivot",),
        )
    with pytest.raises(ValueError, match="out of range"):
        SupportZoneHint(
            low=Decimal("99"),
            center=Decimal("100"),
            high=Decimal("101"),
            invalidation=Decimal("96"),
            score=Decimal("101"),
            sources=("pivot",),
        )
    with pytest.raises(ValueError, match="requires sources"):
        SupportZoneHint(
            low=Decimal("99"),
            center=Decimal("100"),
            high=Decimal("101"),
            invalidation=Decimal("96"),
            score=Decimal("50"),
            sources=(),
        )
    with pytest.raises(ValueError, match="requires a symbol"):
        SupportContext(symbol=" ", daily_bars=())
    with pytest.raises(ValueError, match="context symbol"):
        SupportContext(
            symbol="MSFT",
            daily_bars=(
                _bar(0, open_="1", high="2", low="1", close="2"),
            ),
        )
    with pytest.raises(ValueError, match="daily_bars"):
        SupportContext(
            symbol="TGT",
            daily_bars=(
                _sweep_bars()[0].model_copy(
                    update={"timeframe": BarTimeframe.WEEK_1}
                ),
            ),
        )
