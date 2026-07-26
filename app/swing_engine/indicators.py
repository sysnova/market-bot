"""Decimal technical indicators used by Swing v1."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.contracts import MarketBar

FOUR_PLACES = Decimal("0.0001")
HUNDRED = Decimal("100")


def rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return rounded(sum(values, Decimal("0")) / Decimal(len(values)))


def sma(values: tuple[Decimal, ...], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError(f"SMA({period}) requires {period} values")
    return mean(values[-period:])


def percent_vs(value: Decimal, reference: Decimal) -> Decimal:
    return rounded((value - reference) / reference * HUNDRED)


def rsi(values: tuple[Decimal, ...], period: int = 14) -> Decimal:
    if len(values) < period + 1:
        raise ValueError(f"RSI({period}) requires {period + 1} values")
    window = values[-(period + 1) :]
    changes = tuple(window[index] - window[index - 1] for index in range(1, len(window)))
    gains = sum((max(value, Decimal("0")) for value in changes), Decimal("0"))
    losses = sum((max(-value, Decimal("0")) for value in changes), Decimal("0"))
    if losses == 0:
        return HUNDRED if gains > 0 else Decimal("50")
    relative_strength = gains / losses
    return rounded(HUNDRED - HUNDRED / (Decimal("1") + relative_strength))


def atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    if len(bars) < period + 1:
        raise ValueError(f"ATR({period}) requires {period + 1} bars")
    true_ranges: list[Decimal] = []
    for index in range(len(bars) - period, len(bars)):
        bar = bars[index]
        previous_close = bars[index - 1].close
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return mean(tuple(true_ranges))


def relative_volume(bars: tuple[MarketBar, ...], period: int = 20) -> Decimal | None:
    if len(bars) < period + 1:
        return None
    baseline = mean(tuple(bar.volume for bar in bars[-(period + 1) : -1]))
    if baseline == 0:
        return None
    return rounded(bars[-1].volume / baseline)

