"""Ascending three-pivot Swing channel over completed RTH four-hour bars."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryMaturityLevel,
    MarketBar,
    NamedValue,
    PatternDirection,
    SwingChannelAssessment,
    SwingChannelMaturity,
)

from .models import SwingChannel4HContext

ZERO = Decimal("0")
FOUR_PLACES = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class _Geometry:
    a: int
    b: int
    c: int
    slope: Decimal
    width: Decimal


class SwingChannel4HEngine:
    """Locate support early and keep confirmation alignment observable."""

    engine_id = "swing-channel-4h"
    engine_version = "1.0.0"

    def __init__(
        self,
        *,
        pivot_radius: int = 1,
        minimum_bars: int = 8,
        channel_lookback_bars: int = 60,
        zone_atr: Decimal = Decimal("0.25"),
        invalidation_atr: Decimal = Decimal("0.50"),
    ) -> None:
        if pivot_radius < 1:
            raise ValueError("pivot radius must be positive")
        if minimum_bars < pivot_radius * 2 + 5:
            raise ValueError("minimum bars cannot confirm three channel pivots")
        if channel_lookback_bars < minimum_bars:
            raise ValueError("channel lookback cannot be shorter than minimum bars")
        if zone_atr <= ZERO or invalidation_atr <= zone_atr:
            raise ValueError("channel ATR buffers are out of order")
        self._pivot_radius = pivot_radius
        self._minimum_bars = minimum_bars
        self._lookback = channel_lookback_bars
        self._zone_atr = zone_atr
        self._invalidation_atr = invalidation_atr

    def analyze(self, context: SwingChannel4HContext) -> SwingChannelAssessment:
        symbol = context.symbol.strip().upper()
        bars = tuple(bar for bar in context.bars[-self._lookback :] if bar.is_final)
        if len(bars) < self._minimum_bars:
            raise ValueError("Swing Channel 4H requires more completed bars")
        if any(bar.symbol != symbol for bar in bars):
            raise ValueError("channel bars must belong to the requested symbol")
        if any(bar.timeframe is not BarTimeframe.HOUR_4 for bar in bars):
            raise ValueError("Swing Channel 4H requires 4Hour bars")
        if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
            raise ValueError("channel bars must be strictly chronological")
        if context.current_price <= ZERO:
            raise ValueError("current price must be positive")

        atr14 = _atr(bars)
        geometry = self._geometry(bars)
        current_index = len(bars) - 1
        support = _line(geometry, bars, current_index)
        resistance = support + geometry.width
        middle = support + geometry.width / Decimal("2")
        zone_padding = atr14 * self._zone_atr
        zone_low = max(Decimal("0.0001"), support - zone_padding)
        zone_high = support + zone_padding
        invalidation = max(
            Decimal("0.0001"), support - atr14 * self._invalidation_atr
        )
        touch_indices = _support_touches(
            bars,
            geometry,
            start=geometry.b,
            padding=zone_padding,
            invalidation_padding=atr14 * self._invalidation_atr,
        )
        bounce = _bounce_confirmed(bars, geometry, touch_indices)
        daily_low, daily_high = _daily_swing_zone(context.daily_swing)
        daily_aligned = _daily_swing_aligned(
            context.daily_swing,
            channel_low=zone_low,
            channel_high=zone_high,
            swing_low=daily_low,
            swing_high=daily_high,
        )
        existing_aligned = context.existing_maturity in {
            EntryMaturityLevel.L3,
            EntryMaturityLevel.L4,
        }
        maturity = _maturity(
            price=context.current_price,
            zone_low=zone_low,
            zone_high=zone_high,
            invalidation=invalidation,
            bounce=bounce,
            daily_aligned=daily_aligned,
            existing_aligned=existing_aligned,
        )
        containment = _containment(bars, geometry, atr14 * Decimal("0.25"))
        distance = (context.current_price - support) / atr14
        reasons = _reasons(maturity, daily_aligned, existing_aligned)
        c = bars[geometry.c]
        return SwingChannelAssessment(
            symbol=symbol,
            occurred_at=bars[-1].timestamp,
            engine_version=self.engine_version,
            maturity=maturity,
            current_price=_rounded(context.current_price),
            pivot_a_at=bars[geometry.a].timestamp,
            pivot_a_price=_rounded(bars[geometry.a].low),
            pivot_b_at=bars[geometry.b].timestamp,
            pivot_b_price=_rounded(bars[geometry.b].low),
            pivot_c_at=c.timestamp,
            pivot_c_price=_rounded(c.high),
            support=_rounded(support),
            middle=_rounded(middle),
            resistance=_rounded(resistance),
            zone_low=_rounded(zone_low),
            zone_high=_rounded(zone_high),
            invalidation=_rounded(invalidation),
            slope_per_bar=_rounded(geometry.slope),
            width=_rounded(geometry.width),
            width_atr=_rounded(geometry.width / atr14),
            distance_to_support_atr=_rounded(distance),
            containment_ratio=_rounded(containment),
            support_touch_count=len(touch_indices),
            touch_low=(
                _rounded(bars[touch_indices[-1]].low) if touch_indices else None
            ),
            bounce_confirmed=bounce,
            daily_swing_aligned=daily_aligned,
            existing_maturity_aligned=existing_aligned,
            current_swing_zone_low=_rounded(daily_low) if daily_low is not None else None,
            current_swing_zone_high=(
                _rounded(daily_high) if daily_high is not None else None
            ),
            reasons=reasons,
            metrics=(
                NamedValue(name="atr14_4h", value=_rounded(atr14)),
                NamedValue(name="channel_source", value="confirmed_pivot_lows_parallel_high"),
                NamedValue(name="rth_anchor", value="09:30_America/New_York"),
            ),
            context_hash=_context_hash(bars, context.current_price, context.daily_swing),
        )

    def _geometry(self, bars: tuple[MarketBar, ...]) -> _Geometry:
        lows = _pivot_lows(bars, self._pivot_radius)
        if len(lows) < 2:
            raise ValueError("no confirmed ascending channel pivots")
        a = min(lows[:-1], key=lambda index: (bars[index].low, index))
        b = next(
            (
                index
                for index in lows
                if index > a and bars[index].low > bars[a].low
            ),
            None,
        )
        if b is None or b >= len(bars) - 1:
            raise ValueError("no higher confirmed pivot low after channel origin")
        c = max(range(b + 1, len(bars)), key=lambda index: bars[index].high)
        slope = (bars[b].low - bars[a].low) / Decimal(b - a)
        width = bars[c].high - (bars[a].low + slope * Decimal(c - a))
        if slope <= ZERO or width <= ZERO:
            raise ValueError("ascending channel geometry is invalid")
        return _Geometry(a=a, b=b, c=c, slope=slope, width=width)


def _pivot_lows(bars: tuple[MarketBar, ...], radius: int) -> tuple[int, ...]:
    result: list[int] = []
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        low = bars[index].low
        if all(low <= item.low for item in neighbors) and any(low < item.low for item in neighbors):
            result.append(index)
    return tuple(result)


def _line(geometry: _Geometry, bars: tuple[MarketBar, ...], index: int) -> Decimal:
    return bars[geometry.a].low + geometry.slope * Decimal(index - geometry.a)


def _support_touches(
    bars: tuple[MarketBar, ...],
    geometry: _Geometry,
    *,
    start: int,
    padding: Decimal,
    invalidation_padding: Decimal,
) -> tuple[int, ...]:
    return tuple(
        index
        for index in range(start, len(bars))
        if bars[index].low <= _line(geometry, bars, index) + padding
        and bars[index].high >= _line(geometry, bars, index) - padding
        and bars[index].close >= _line(geometry, bars, index) - invalidation_padding
    )


def _bounce_confirmed(
    bars: tuple[MarketBar, ...], geometry: _Geometry, touches: tuple[int, ...]
) -> bool:
    latest = len(bars) - 1
    prior = latest - 1
    if prior not in touches:
        return False
    current = bars[latest]
    touched = bars[prior]
    return (
        current.low > touched.low
        and current.close > current.open
        and current.close > touched.close
        and current.close > _line(geometry, bars, latest)
    )


def _daily_swing_zone(result: AnalysisResult | None) -> tuple[Decimal | None, Decimal | None]:
    if result is None or result.horizon is not AnalysisHorizon.SWING:
        return None, None
    metrics = {item.name: item.value for item in result.metrics}
    low = metrics.get("entry_zone_low")
    high = metrics.get("entry_zone_high")
    return (
        low if isinstance(low, Decimal) else None,
        high if isinstance(high, Decimal) else None,
    )


def _daily_swing_aligned(
    result: AnalysisResult | None,
    *,
    channel_low: Decimal,
    channel_high: Decimal,
    swing_low: Decimal | None,
    swing_high: Decimal | None,
) -> bool:
    return bool(
        result is not None
        and result.horizon is AnalysisHorizon.SWING
        and result.direction is PatternDirection.BULLISH
        and result.verdict is AnalysisVerdict.FAVORABLE
        and swing_low is not None
        and swing_high is not None
        and swing_low <= channel_high
        and swing_high >= channel_low
    )


def _maturity(
    *,
    price: Decimal,
    zone_low: Decimal,
    zone_high: Decimal,
    invalidation: Decimal,
    bounce: bool,
    daily_aligned: bool,
    existing_aligned: bool,
) -> SwingChannelMaturity:
    if price <= invalidation:
        return SwingChannelMaturity.INVALIDATED
    if bounce and daily_aligned and existing_aligned:
        return SwingChannelMaturity.L4
    if bounce and daily_aligned:
        return SwingChannelMaturity.L3
    if bounce:
        return SwingChannelMaturity.L2_4H
    if zone_low <= price <= zone_high:
        return SwingChannelMaturity.IN_ZONE_4H
    return SwingChannelMaturity.ARMED


def _containment(
    bars: tuple[MarketBar, ...], geometry: _Geometry, tolerance: Decimal
) -> Decimal:
    sample = bars[geometry.a :]
    inside = sum(
        bar.low >= _line(geometry, bars, index) - tolerance
        and bar.high <= _line(geometry, bars, index) + geometry.width + tolerance
        for index, bar in enumerate(bars)
        if index >= geometry.a
    )
    return Decimal(inside) / Decimal(len(sample))


def _reasons(
    maturity: SwingChannelMaturity, daily_aligned: bool, existing_aligned: bool
) -> tuple[str, ...]:
    reasons = {
        SwingChannelMaturity.ARMED: ["ascending_channel_armed"],
        SwingChannelMaturity.IN_ZONE_4H: ["projected_support_touched"],
        SwingChannelMaturity.L2_4H: ["four_hour_higher_low_bounce"],
        SwingChannelMaturity.L3: ["four_hour_bounce_aligned_with_daily_swing"],
        SwingChannelMaturity.L4: ["four_hour_and_existing_maturity_aligned"],
        SwingChannelMaturity.INVALIDATED: ["projected_support_invalidation_breached"],
    }[maturity]
    if daily_aligned:
        reasons.append("daily_swing_zone_overlap")
    if existing_aligned:
        reasons.append("existing_l3_l4_confirmation")
    return tuple(reasons)


def _atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars)
    )
    sample = ranges[-period:]
    if not sample:
        raise ValueError("channel ATR requires multiple bars")
    value = sum(sample, ZERO) / Decimal(len(sample))
    if value <= ZERO:
        raise ValueError("channel ATR must be positive")
    return value


def _context_hash(
    bars: tuple[MarketBar, ...], price: Decimal, daily_swing: AnalysisResult | None
) -> str:
    payload = {
        "bars": [
            [
                bar.timestamp.isoformat(),
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
            ]
            for bar in bars
        ],
        "price": str(price),
        "daily_swing": str(daily_swing.analysis_id) if daily_swing is not None else None,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
