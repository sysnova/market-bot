"""Decimal-only technical calculations for deterministic long-term analysis."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import MarketBar

FOUR_PLACES = Decimal("0.0001")
HUNDRED = Decimal("100")


def rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires at least one value")
    return rounded(sum(values, Decimal("0")) / Decimal(len(values)))


def sma(values: tuple[Decimal, ...], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError(f"SMA({period}) requires {period} values")
    return mean(values[-period:])


def percent_vs(value: Decimal, reference: Decimal) -> Decimal:
    return rounded(((value - reference) / reference) * HUNDRED)


def rsi(values: tuple[Decimal, ...], period: int = 14) -> Decimal:
    if len(values) < period + 1:
        raise ValueError(f"RSI({period}) requires {period + 1} values")
    window = values[-(period + 1) :]
    changes = tuple(right - left for left, right in pairwise(window))
    gains = tuple(max(change, Decimal("0")) for change in changes)
    losses = tuple(max(-change, Decimal("0")) for change in changes)
    average_gain = sum(gains, Decimal("0")) / Decimal(period)
    average_loss = sum(losses, Decimal("0")) / Decimal(period)
    if average_loss == 0:
        return HUNDRED if average_gain > 0 else Decimal("50")
    relative_strength = average_gain / average_loss
    return rounded(HUNDRED - (HUNDRED / (Decimal("1") + relative_strength)))


def relative_volume(bars: tuple[MarketBar, ...], period: int) -> Decimal | None:
    if len(bars) < period + 1:
        return None
    baseline = mean(tuple(Decimal(bar.volume) for bar in bars[-(period + 1) : -1]))
    if baseline == 0:
        return None
    return rounded(Decimal(bars[-1].volume) / baseline)


def distribution_weeks(bars: tuple[MarketBar, ...], average_volume: Decimal | None) -> int:
    if average_volume is None:
        return 0
    threshold = average_volume * Decimal("1.2")
    return sum(
        bar.close < bar.open and Decimal(bar.volume) >= threshold for bar in bars[-8:]
    )


def has_higher_lows(bars: tuple[MarketBar, ...]) -> bool:
    recent = bars[-12:]
    if len(recent) < 12:
        return False
    return min(bar.low for bar in recent[6:]) > min(bar.low for bar in recent[:6])
