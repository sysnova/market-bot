from decimal import Decimal

from app.contracts import AnalysisVerdict, BarTimeframe, PatternDirection
from app.intraday_engine import IntradayEngineV5, IntradayEngineV6
from app.intraday_engine.models import IntradayContext

from .helpers import trend_bars


def _metric(result: object, name: str) -> object:
    return next(item.value for item in result.metrics if item.name == name)  # type: ignore[attr-defined]


def _ema20_extended_local_breakdown(*, final_move: str = "-0.10") -> IntradayContext:
    minute_bars = trend_bars(
        symbol="ASTS",
        start=Decimal("165"),
        step=Decimal("-0.08"),
        final_move=Decimal(final_move),
        base_volume=Decimal("1000"),
        final_volume=Decimal("3000"),
    )
    five_minute_bars = trend_bars(
        symbol="ASTS",
        start=Decimal("166"),
        step=Decimal("-0.15"),
        final_move=Decimal("-0.30"),
        base_volume=Decimal("5000"),
        final_volume=Decimal("7000"),
        count=20,
        timeframe=BarTimeframe.MINUTE_5,
    )
    return IntradayContext(
        symbol="ASTS",
        as_of=max(minute_bars[-1].timestamp, five_minute_bars[-1].timestamp),
        minute_bars=minute_bars,
        five_minute_bars=five_minute_bars,
    )


def test_v6_confirms_local_breakdown_even_when_ema20_is_more_than_two_atr_away() -> None:
    context = _ema20_extended_local_breakdown()

    previous = IntradayEngineV5().analyze(context)
    result = IntradayEngineV6().analyze(context)

    assert _metric(previous, "short_breakdown_extension_atr") < Decimal("0.50")
    assert _metric(previous, "short_ema20_extension_atr") > Decimal("2.00")
    assert _metric(previous, "short_mature_confirmation_gate_passed") is False
    assert result.engine_version == "6.0.0"
    assert result.direction is PatternDirection.BEARISH
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert _metric(result, "short_entry_efficiency_gate_passed") is True
    assert _metric(result, "short_mature_confirmation_gate_passed") is True
    assert _metric(result, "short_ema20_extension_warning") is True
    assert "short_extended_below_ema20" in result.reasons


def test_v6_still_rejects_a_short_chased_beyond_the_local_trigger_window() -> None:
    result = IntradayEngineV6().analyze(
        _ema20_extended_local_breakdown(final_move="-0.25")
    )

    assert _metric(result, "short_breakdown_extension_atr") > Decimal("0.50")
    assert _metric(result, "short_entry_efficiency_gate_passed") is False
    assert _metric(result, "short_mature_confirmation_gate_passed") is False
    assert result.verdict is AnalysisVerdict.WATCH
