"""Daily EMA21/SMA50 levels and explicit recovery evidence, without future daily closes."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.contracts import MarketBar

from .models import Swing4HGeriContext
from .tactical_levels import NY, completed_at, pivots


@dataclass(frozen=True)
class AverageSnapshot:
    ema21: Decimal
    sma50: Decimal
    ema21_slope: Decimal
    sma50_slope: Decimal | None
    as_of: datetime


def average_snapshot(bars: tuple[MarketBar, ...], at: datetime) -> AverageSnapshot | None:
    known = tuple(b for b in bars if b.is_final and completed_at(b) <= at)
    if len(known) < 50:
        return None
    closes = tuple(b.close for b in known)
    ema = sum(closes[:21], Decimal(0)) / 21
    previous = ema
    for close in closes[21:]:
        previous = ema
        ema += Decimal(2) / 22 * (close - ema)
    sma = sum(closes[-50:], Decimal(0)) / 50
    slope = (closes[-1] - closes[-51]) / 50 if len(closes) > 50 else None
    return AverageSnapshot(ema, sma, ema - previous, slope, completed_at(known[-1]))


def same_slot_rvol(
    bars: tuple[MarketBar, ...], latest: MarketBar, sessions: int = 5
) -> Decimal | None:
    local = latest.timestamp.astimezone(NY)
    baseline = tuple(
        b.volume
        for b in bars
        if b.timestamp.astimezone(NY).date() < local.date()
        and b.timestamp.astimezone(NY).time() == local.time()
    )[-sessions:]
    if len(baseline) < sessions or sum(baseline, Decimal(0)) <= 0:
        return None
    return latest.volume / (sum(baseline, Decimal(0)) / sessions)


def average_state(
    bars: tuple[MarketBar, ...],
    level: Decimal,
    padding: Decimal,
    volume_confirmed: bool | Callable[[MarketBar], bool],
) -> str:
    if not bars:
        return "UNAVAILABLE"
    breakout: MarketBar | None = None
    accepted = False
    retested = False
    pending_volume = False
    previous: MarketBar | None = None
    for bar in bars:
        if previous is not None and bar.timestamp - previous.timestamp != timedelta(minutes=15):
            breakout = None
            accepted = retested = pending_volume = False
        if bar.close < level:
            breakout = None
            accepted = retested = pending_volume = False
        elif accepted:
            if bar.low <= level + padding and bar.close > level:
                retested = True
        elif breakout is not None:
            if bar.low <= breakout.low:
                breakout = None
                pending_volume = False
            elif bar.close > level:
                volume_ok = (
                    volume_confirmed(bar) if callable(volume_confirmed) else volume_confirmed
                )
                accepted = volume_ok
                pending_volume = not volume_ok
        elif (
            bar.close > level + padding
            and bar.close > bar.open
            and (bar.open <= level or (previous is not None and previous.close <= level))
        ):
            breakout = bar
        previous = bar
    if bars[-1].close < level:
        return "RESISTANCE_PENDING"
    if retested:
        return "RETEST_CONFIRMED"
    if accepted:
        return "RECLAIM_CONFIRMED"
    if pending_volume:
        return "VOLUME_PENDING"
    return "RECLAIM_PENDING" if breakout is not None else "ABOVE_UNCONFIRMED"


def structural_target(
    context: Swing4HGeriContext,
    price: Decimal,
    at: datetime,
    lookback: int = 20,
) -> Decimal | None:
    # Recent confirmed DAILY swing highs may cap a trade before a moving average.
    # Old lows and minor 4H pivots remain visible separately, without equal target priority.
    known = tuple(b for b in context.daily_bars if completed_at(b) <= at)
    levels = tuple(
        known[i].high
        for i in pivots(known, low=False)
        if i >= len(known) - lookback
        and known[i].high > price
        and not any(b.close >= known[i].high for b in known[i + 1 :])
    )
    return min(levels) if levels else None
