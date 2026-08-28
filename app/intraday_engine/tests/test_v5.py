from decimal import Decimal

from app.contracts import AnalysisVerdict, BarTimeframe, PatternDirection
from app.intraday_engine import IntradayEngineV5
from app.intraday_engine.models import IntradayContext

from .helpers import trend_bars


def _metric(result: object, name: str) -> object:
    return next(item.value for item in result.metrics if item.name == name)  # type: ignore[attr-defined]


def _efficient_bearish_context() -> IntradayContext:
    minute_bars = list(
        trend_bars(
            symbol="ASTS",
            start=Decimal("160"),
            step=Decimal("0"),
            final_move=Decimal("-0.01"),
            base_volume=Decimal("1000"),
            final_volume=Decimal("1000"),
        )
    )
    for index, close in enumerate(
        map(Decimal, ("160.00", "159.94", "159.88", "159.82", "159.756")),
        start=55,
    ):
        open_price = minute_bars[index - 1].close
        minute_bars[index] = minute_bars[index].model_copy(
            update={
                "open": open_price,
                "high": max(open_price, close) + Decimal("0.12"),
                "low": close - Decimal("0.02"),
                "close": close,
                "volume": Decimal("2600") if index == 59 else Decimal("1000"),
            }
        )
    five_minute_bars = trend_bars(
        symbol="ASTS",
        start=Decimal("165"),
        step=Decimal("-0.12"),
        final_move=Decimal("-0.30"),
        base_volume=Decimal("5000"),
        final_volume=Decimal("7000"),
        count=20,
        timeframe=BarTimeframe.MINUTE_5,
    )
    return IntradayContext(
        symbol="ASTS",
        as_of=max(minute_bars[-1].timestamp, five_minute_bars[-1].timestamp),
        minute_bars=tuple(minute_bars),
        five_minute_bars=five_minute_bars,
    )


def test_v5_confirms_an_efficient_strong_lower_high_short() -> None:
    result = IntradayEngineV5().analyze(_efficient_bearish_context())

    assert result.engine_version == "5.0.0"
    assert result.direction is PatternDirection.BEARISH
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert _metric(result, "setup") == "bearish_breakdown"
    assert _metric(result, "five_minute_lower_high") is True
    assert _metric(result, "short_confirmation_gate_passed") is True
    assert _metric(result, "short_entry_efficiency_gate_passed") is True
    assert _metric(result, "short_mature_confirmation_gate_passed") is True
    price = Decimal(str(_metric(result, "reference_price")))
    assert Decimal(str(_metric(result, "invalidation_level"))) > price
    assert Decimal(str(_metric(result, "objective_level"))) < price


def test_v5_never_marks_a_bullish_setup_as_short() -> None:
    context = _efficient_bearish_context()
    bullish = tuple(
        bar.model_copy(
            update={
                "open": Decimal("160"),
                "high": Decimal("160.20"),
                "low": Decimal("159.95"),
                "close": Decimal("160.18"),
            }
        )
        for bar in context.minute_bars
    )
    result = IntradayEngineV5().analyze(
        context.model_copy(update={"minute_bars": bullish})
    )

    assert _metric(result, "short_mature_confirmation_gate_passed") is False
