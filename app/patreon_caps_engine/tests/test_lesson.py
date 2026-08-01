from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.contracts import BarTimeframe, MarketBar
from app.patreon_caps_engine import evaluate_lesson, load_patreon_caps_policy
from app.patreon_caps_engine.lesson import _ascending_triangle, _wave_structure
from app.patreon_caps_engine.models import PatreonCapsPolicy

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(
    closes: tuple[Decimal, ...], timeframe: BarTimeframe = BarTimeframe.DAY_1
) -> tuple[MarketBar, ...]:
    spacing = timedelta(hours=1) if timeframe is BarTimeframe.HOUR_1 else timedelta(days=1)
    return tuple(
        MarketBar(
            symbol="TEST",
            timeframe=timeframe,
            timestamp=NOW + spacing * index,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("100"),
            source="fixture",
            feed="test",
        )
        for index, close in enumerate(closes)
    )


def _policy() -> PatreonCapsPolicy:
    return load_patreon_caps_policy(Path("configs/rules/patreon_caps/1.1.0.yaml"))


def test_ma_lesson_rewards_golden_cross_and_blocks_death_cross() -> None:
    bullish = (Decimal("100"),) * 200 + tuple(
        Decimal(100 + index) for index in range(1, 6)
    )
    bearish = (Decimal("100"),) * 200 + tuple(
        Decimal(100 - index) for index in range(1, 6)
    )
    hourly = _bars(bullish, BarTimeframe.HOUR_1)

    golden = evaluate_lesson(_bars(bullish), hourly, atr14=Decimal("2"), policy=_policy())
    death = evaluate_lesson(_bars(bearish), hourly, atr14=Decimal("2"), policy=_policy())

    assert golden.golden_cross is True
    assert golden.gate_passed is True
    assert golden.score > death.score
    assert death.death_cross is True
    assert death.gate_passed is False


def test_ascending_triangle_requires_flat_highs_rising_lows_breakout_and_retest() -> None:
    bars = list(_bars(tuple(Decimal("94") + Decimal(index) / 100 for index in range(80))))
    updates = {
        50: {"high": Decimal("100"), "low": Decimal("95"), "close": Decimal("96")},
        55: {"high": Decimal("95"), "low": Decimal("90"), "close": Decimal("94")},
        60: {"high": Decimal("100.1"), "low": Decimal("95"), "close": Decimal("97")},
        65: {"high": Decimal("95"), "low": Decimal("92"), "close": Decimal("95")},
        70: {"high": Decimal("99.9"), "low": Decimal("96"), "close": Decimal("97")},
        74: {"high": Decimal("95.5"), "low": Decimal("94"), "close": Decimal("95")},
        77: {
            "high": Decimal("101"),
            "low": Decimal("99.8"),
            "close": Decimal("100.8"),
            "volume": Decimal("200"),
        },
        78: {"high": Decimal("101.2"), "low": Decimal("100"), "close": Decimal("100.5")},
        79: {"high": Decimal("101.3"), "low": Decimal("100.1"), "close": Decimal("101")},
    }
    for index, update in updates.items():
        bars[index] = bars[index].model_copy(update=update)

    triangle, breakout, retest, resistance = _ascending_triangle(
        tuple(bars),
        atr14=Decimal("2"),
        lookback=80,
        tolerance_atr=Decimal("0.35"),
    )

    assert triangle is True
    assert breakout is True
    assert retest is True
    assert Decimal("99.9") <= resistance <= Decimal("100.1")


def test_wave_two_holds_0618_and_retests_wave_one_high() -> None:
    bars = list(_bars((Decimal("105"),) * 50))
    bars[20] = bars[20].model_copy(
        update={
            "open": Decimal("92"),
            "high": Decimal("93"),
            "low": Decimal("90"),
            "close": Decimal("92"),
        }
    )
    bars[30] = bars[30].model_copy(
        update={
            "open": Decimal("108"),
            "high": Decimal("110"),
            "low": Decimal("107"),
            "close": Decimal("109"),
        }
    )
    bars[35] = bars[35].model_copy(
        update={
            "open": Decimal("99"),
            "high": Decimal("100"),
            "low": Decimal("97.64"),
            "close": Decimal("99"),
        }
    )
    bars[-1] = bars[-1].model_copy(
        update={
            "open": Decimal("109"),
            "high": Decimal("110.2"),
            "low": Decimal("108.5"),
            "close": Decimal("109.8"),
        }
    )

    hold, retest, fib0618, wave1_high = _wave_structure(
        tuple(bars),
        atr14=Decimal("2"),
        tolerance_atr=Decimal("0.15"),
    )

    assert fib0618 == Decimal("97.6400")
    assert wave1_high == Decimal("110")
    assert hold is True
    assert retest is True
