"""Causal momentum observations; these measurements never authorize an entry."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo

from app.contracts import BarTimeframe, MarketBar, NamedValue

_NY = ZoneInfo("America/New_York")
_PRECISION = Decimal("0.00000001")


@dataclass(frozen=True)
class MacdValues:
    line: Decimal
    signal: Decimal
    histogram: Decimal
    previous_histogram: Decimal


def calculate_macd(
    closes: tuple[Decimal, ...], *, fast: int = 12, slow: int = 26, signal: int = 9
) -> MacdValues | None:
    if not 1 <= fast < slow or signal < 1:
        raise ValueError("MACD requires 1 <= fast < slow and a positive signal period")
    if len(closes) < slow + signal:
        return None
    fast_values = _ema(closes, fast)[slow - fast :]
    slow_values = _ema(closes, slow)
    line = tuple(a - b for a, b in zip(fast_values, slow_values, strict=True))
    signal_values = _ema(line, signal)
    histogram = tuple(a - b for a, b in zip(line[signal - 1 :], signal_values, strict=True))
    return MacdValues(line[-1], signal_values[-1], histogram[-1], histogram[-2])


def _ema(values: tuple[Decimal, ...], period: int) -> tuple[Decimal, ...]:
    value = sum(values[:period], Decimal(0)) / period
    out = [value]
    alpha = Decimal(2) / (period + 1)
    for observation in values[period:]:
        value += alpha * (observation - value)
        out.append(value)
    return tuple(out)


def completed_at(bar: MarketBar) -> datetime:
    local = bar.timestamp.astimezone(_NY)
    if bar.timeframe is BarTimeframe.DAY_1:
        end = time(16)
    elif bar.timeframe is BarTimeframe.HOUR_4:
        # Same RTH channel convention as integration's four-hour aggregator.
        if local.time() not in {time(9, 30), time(13, 30)}:
            raise ValueError("SwingTrade 4H observations require RTH channel bars")
        end = time(13, 30) if local.time() == time(9, 30) else time(16)
    else:
        raise ValueError("MACD observations require daily or four-hour bars")
    return datetime.combine(local.date(), end, _NY).astimezone(UTC)


def macd_metrics(
    bars: tuple[MarketBar, ...],
    *,
    symbol: str,
    timeframe: BarTimeframe,
    as_of: datetime,
    prefix: str,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    max_age_hours: int = 96,
) -> tuple[NamedValue, ...]:
    if any(b.symbol != symbol or b.timeframe is not timeframe for b in bars):
        raise ValueError("MACD history must match symbol and timeframe")
    if any(a.timestamp >= b.timestamp for a, b in pairwise(bars)):
        raise ValueError("MACD history must be chronological and unique")
    # Discard future/non-final bars before inspecting their channel or close.
    history = tuple(
        b for b in bars if b.is_final and b.timestamp <= as_of and completed_at(b) <= as_of
    )
    last = completed_at(history[-1]) if history else None
    value = calculate_macd(tuple(b.close for b in history), fast=fast, slow=slow, signal=signal)
    status = "AVAILABLE" if value is not None else "INSUFFICIENT_HISTORY"
    if last is not None and as_of - last > timedelta(hours=max_age_hours):
        status, value = "STALE", None
    histogram = value.histogram.quantize(_PRECISION) if value else None
    previous = value.previous_histogram.quantize(_PRECISION) if value else None
    direction = "UNKNOWN"
    if histogram is not None and previous is not None:
        direction = (
            "IMPROVING"
            if histogram > previous
            else ("DETERIORATING" if histogram < previous else "FLAT")
        )
    fields: dict[str, str | int | Decimal | bool | None] = {
        "status": status,
        "samples": len(history),
        "required_samples": slow + signal,
        "as_of": last.isoformat() if last else None,
        "line": value.line.quantize(_PRECISION) if value else None,
        "signal": value.signal.quantize(_PRECISION) if value else None,
        "histogram": histogram,
        "previous_histogram": previous,
        "direction": direction,
        "above_signal": histogram > 0 if histogram is not None else None,
    }
    return tuple(NamedValue(name=f"macd_{prefix}_{key}", value=v) for key, v in fields.items())


def local_breakout(
    bars: tuple[MarketBar, ...], *, as_of: datetime, reference_bars: int
) -> tuple[bool | None, Decimal | None]:
    history = tuple(b for b in bars if b.is_final and b.timestamp + timedelta(minutes=15) <= as_of)
    window = history[-reference_bars - 1 :]
    if (
        len(window) < reference_bars + 1
        or any(b.timestamp - a.timestamp != timedelta(minutes=15) for a, b in pairwise(window))
        or any(
            b.timestamp.astimezone(_NY).date() != window[-1].timestamp.astimezone(_NY).date()
            for b in window
        )
    ):
        return None, None
    reference = max(b.high for b in window[:-1])
    return window[-1].close > reference, reference
