"""Decimal-only technical calculations for Intraday v1."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import MarketBar

HUNDRED = Decimal("100")


def mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return sum(values, Decimal("0")) / Decimal(len(values))


def ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    if not values or period < 1:
        raise ValueError("EMA requires values and a positive period")
    alpha = Decimal("2") / Decimal(period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * alpha + current * (Decimal("1") - alpha)
    return current


def session_vwap(bars: tuple[MarketBar, ...]) -> Decimal:
    if not bars:
        raise ValueError("VWAP requires bars")
    total_volume = sum((bar.volume for bar in bars), Decimal("0"))
    if total_volume <= 0:
        return mean(tuple(bar.close for bar in bars))
    notional = Decimal("0")
    for bar in bars:
        reference = bar.vwap or (bar.high + bar.low + bar.close) / Decimal("3")
        notional += reference * bar.volume
    return notional / total_volume


def relative_volume(bars: tuple[MarketBar, ...], lookback: int = 20) -> Decimal:
    if len(bars) < 2:
        return Decimal("0")
    prior = bars[max(0, len(bars) - lookback - 1) : -1]
    average = mean(tuple(bar.volume for bar in prior))
    if average <= 0:
        return Decimal("0")
    return bars[-1].volume / average


def atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    if len(bars) < 2:
        raise ValueError("ATR requires at least two bars")
    true_ranges: list[Decimal] = []
    window = bars[-(period + 1) :]
    for previous, current in pairwise(window):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return mean(tuple(true_ranges))


def percent_vs(value: Decimal, reference: Decimal) -> Decimal:
    if reference == 0:
        raise ValueError("percentage reference cannot be zero")
    return (value - reference) / reference * HUNDRED


def rounded(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
