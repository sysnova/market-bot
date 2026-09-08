"""Causal price levels and geometry shared by separate LONG recovery and SHORT lanes."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

from app.contracts import BarTimeframe, MarketBar, NamedValue, TradeSide

from .models import Swing4HGeriContext

NY = ZoneInfo("America/New_York")
ZERO = Decimal(0)


def completed_at(bar: MarketBar) -> datetime:
    local = bar.timestamp.astimezone(NY)
    if bar.timeframe is BarTimeframe.DAY_1:
        return datetime.combine(local.date(), time(16), NY).astimezone(UTC)
    if bar.timeframe is BarTimeframe.HOUR_4:
        if local.time() in {time(9, 30), time(13, 30)}:
            end = time(13, 30) if local.time() == time(9, 30) else time(16)
            return datetime.combine(local.date(), end, NY).astimezone(UTC)
        return bar.timestamp + timedelta(hours=4)
    return bar.timestamp + timedelta(minutes=15)


def validate_context(context: Swing4HGeriContext) -> None:
    at = context.current_price_at
    if at is None:
        raise ValueError("tactical lanes require current_price_at")
    for label, bars, timeframe in (
        ("daily", context.daily_bars, BarTimeframe.DAY_1),
        ("structural", context.bars, BarTimeframe.HOUR_4),
        ("confirmation", context.confirmation_bars, BarTimeframe.MINUTE_15),
    ):
        if any(
            b.timeframe is not timeframe
            or b.symbol != context.symbol
            or not b.is_final
            or completed_at(b) > at
            for b in bars
        ):
            raise ValueError(f"{label} evidence must be completed, causal and match symbol")
        if any(b.timestamp <= a.timestamp for a, b in pairwise(bars)):
            raise ValueError(f"{label} evidence must be chronological")


def pivots(bars: tuple[MarketBar, ...], *, low: bool, radius: int = 1) -> tuple[int, ...]:
    result: list[int] = []
    for i in range(radius, len(bars) - radius):
        value = bars[i].low if low else bars[i].high
        neighbors = (*bars[i - radius : i], *bars[i + 1 : i + radius + 1])
        values = tuple(b.low if low else b.high for b in neighbors)
        if (
            all(value <= v for v in values) and any(value < v for v in values)
            if low
            else all(value >= v for v in values) and any(value > v for v in values)
        ):
            result.append(i)
    return tuple(result)


def obstacles(bars: tuple[MarketBar, ...], side: TradeSide) -> tuple[Decimal, ...]:
    """Unreclaimed broken supports become overhead obstacles; inverse for SHORT."""
    result: set[Decimal] = set()
    for low in (True, False):
        for i in pivots(bars, low=low):
            level = bars[i].low if low else bars[i].high
            closes = tuple(b.close for b in bars[i + 1 :])
            above = closes[-1] >= level
            if (side is TradeSide.LONG and not above) or (side is TradeSide.SHORT and above):
                result.add(level)
    return tuple(sorted(result))


def directional_target(
    side: TradeSide,
    entry: Decimal,
    levels: tuple[Decimal, ...],
) -> Decimal | None:
    candidates = (
        tuple(v for v in levels if v > entry)
        if side is TradeSide.LONG
        else tuple(v for v in levels if v < entry)
    )
    if not candidates:
        return None
    return min(candidates) if side is TradeSide.LONG else max(candidates)


def directional_rr(
    side: TradeSide,
    entry: Decimal,
    stop: Decimal,
    target: Decimal | None,
) -> Decimal | None:
    if target is None or min(entry, stop, target) <= ZERO:
        return None
    risk, reward = (
        (entry - stop, target - entry) if side is TradeSide.LONG else (stop - entry, entry - target)
    )
    return reward / risk if risk > ZERO and reward > ZERO else None


def to_metrics(values: dict[str, object]) -> tuple[NamedValue, ...]:
    return tuple(NamedValue(name=k, value=v) for k, v in values.items())
