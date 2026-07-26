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


def anchored_vwap(bars: tuple[MarketBar, ...], anchor_index: int) -> Decimal:
    """Return volume-weighted price from an inclusive daily-bar anchor."""
    if not bars:
        raise ValueError("anchored VWAP requires bars")
    if anchor_index < 0 or anchor_index >= len(bars):
        raise ValueError("anchor_index must reference an available bar")

    anchored_bars = bars[anchor_index:]
    prices = tuple(
        bar.vwap if bar.vwap is not None else (bar.high + bar.low + bar.close) / Decimal("3")
        for bar in anchored_bars
    )
    total_volume = sum((bar.volume for bar in anchored_bars), Decimal("0"))
    if total_volume == 0:
        return mean(prices)
    weighted_total = sum(
        (price * bar.volume for price, bar in zip(prices, anchored_bars, strict=True)),
        Decimal("0"),
    )
    return rounded(weighted_total / total_volume)


def last_confirmed_pivot_low_index(
    bars: tuple[MarketBar, ...], *, radius: int = 2, lookback: int = 60
) -> int | None:
    """Find the latest local low with completed bars on both sides."""
    if radius < 1:
        raise ValueError("pivot radius must be positive")
    if lookback < radius * 2 + 1:
        raise ValueError("pivot lookback is too short")
    if len(bars) < radius * 2 + 1:
        return None

    start = max(radius, len(bars) - lookback)
    for index in range(len(bars) - radius - 1, start - 1, -1):
        neighbors = (
            *bars[index - radius : index],
            *bars[index + 1 : index + radius + 1],
        )
        low = bars[index].low
        if all(low <= neighbor.low for neighbor in neighbors) and any(
            low < neighbor.low for neighbor in neighbors
        ):
            return index
    return None


def last_breakout_index(
    bars: tuple[MarketBar, ...],
    *,
    resistance_period: int = 20,
    lookback: int = 60,
    confirmation_percent: Decimal = Decimal("0.003"),
) -> int | None:
    """Find the latest final daily close confirmed above prior resistance."""
    if resistance_period < 1:
        raise ValueError("resistance period must be positive")
    if lookback < 1:
        raise ValueError("breakout lookback must be positive")
    if len(bars) <= resistance_period:
        return None

    start = max(resistance_period, len(bars) - lookback)
    for index in range(len(bars) - 1, start - 1, -1):
        resistance = max(bar.high for bar in bars[index - resistance_period : index])
        if bars[index].close >= resistance * (Decimal("1") + confirmation_percent):
            return index
    return None
