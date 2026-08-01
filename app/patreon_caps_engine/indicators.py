"""Decimal-only technical primitives owned by PatreonCaps."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import MarketBar

FOUR_PLACES = Decimal("0.0001")
ZERO = Decimal()


def rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return rounded(sum(values, ZERO) / Decimal(len(values)))


def sma(values: tuple[Decimal, ...], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError(f"SMA({period}) requires {period} values")
    return mean(values[-period:])


def atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    if len(bars) < period + 1:
        raise ValueError(f"ATR({period}) requires {period + 1} bars")
    ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars[-(period + 1) :])
    )
    return mean(ranges)


def relative_volume(bars: tuple[MarketBar, ...], period: int = 20) -> Decimal:
    if len(bars) < period + 1:
        return ZERO
    baseline = mean(tuple(bar.volume for bar in bars[-(period + 1) : -1]))
    return ZERO if baseline <= ZERO else rounded(bars[-1].volume / baseline)


def anchored_vwap(bars: tuple[MarketBar, ...], anchor_index: int) -> Decimal:
    if not 0 <= anchor_index < len(bars):
        raise ValueError("anchor_index must reference an available bar")
    selected = bars[anchor_index:]
    volume = sum((bar.volume for bar in selected), ZERO)
    prices = tuple(
        bar.vwap if bar.vwap is not None else (bar.high + bar.low + bar.close) / Decimal("3")
        for bar in selected
    )
    if volume <= ZERO:
        return mean(prices)
    return rounded(
        sum(
            (price * bar.volume for price, bar in zip(prices, selected, strict=True)),
            ZERO,
        )
        / volume
    )


def confirmed_pivot_indices(
    bars: tuple[MarketBar, ...], *, radius: int = 2
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if radius < 1:
        raise ValueError("pivot radius must be positive")
    lows: list[int] = []
    highs: list[int] = []
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        if all(bars[index].low <= bar.low for bar in neighbors) and any(
            bars[index].low < bar.low for bar in neighbors
        ):
            lows.append(index)
        if all(bars[index].high >= bar.high for bar in neighbors) and any(
            bars[index].high > bar.high for bar in neighbors
        ):
            highs.append(index)
    return tuple(lows), tuple(highs)


def last_breakout_index(
    bars: tuple[MarketBar, ...], *, resistance_period: int = 20
) -> int | None:
    if len(bars) <= resistance_period:
        return None
    for index in range(len(bars) - 1, resistance_period - 1, -1):
        resistance = max(bar.high for bar in bars[index - resistance_period : index])
        if bars[index].close >= resistance * Decimal("1.003"):
            return index
    return None


def fibonacci_levels(
    bars: tuple[MarketBar, ...], *, atr14: Decimal
) -> dict[str, Decimal] | None:
    impulse = confirmed_impulse_indices(bars, atr14=atr14)
    if impulse is None:
        return None
    low_index, high_index = impulse
    low = bars[low_index].low
    high = bars[high_index].high
    impulse_range = high - low
    return {
        f"fib_0_{str(ratio).split('.')[1]}": rounded(high - impulse_range * ratio)
        for ratio in (
            Decimal("0.382"),
            Decimal("0.500"),
            Decimal("0.618"),
            Decimal("0.786"),
        )
    }


def confirmed_impulse_indices(
    bars: tuple[MarketBar, ...], *, atr14: Decimal
) -> tuple[int, int] | None:
    """Return the latest confirmed low-to-high impulse without look-ahead pivots."""
    lows, highs = confirmed_pivot_indices(bars)
    for high_index in reversed(highs):
        low_indices = tuple(index for index in lows if index <= high_index - 5)
        if not low_indices:
            continue
        low_index = low_indices[-1]
        low = bars[low_index].low
        high = bars[high_index].high
        impulse = high - low
        if impulse < atr14 * Decimal("2") or bars[-1].close < low:
            continue
        return low_index, high_index
    return None


def rsi(values: tuple[Decimal, ...], period: int = 14) -> Decimal:
    if len(values) < period + 1:
        raise ValueError(f"RSI({period}) requires {period + 1} values")
    changes = tuple(right - left for left, right in pairwise(values[-(period + 1) :]))
    gains = sum((max(item, ZERO) for item in changes), ZERO)
    losses = sum((max(-item, ZERO) for item in changes), ZERO)
    if losses == ZERO:
        return Decimal("100") if gains > ZERO else Decimal("50")
    strength = gains / losses
    return rounded(Decimal("100") - Decimal("100") / (Decimal("1") + strength))
