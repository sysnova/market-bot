"""Causal horizontal-level reconstruction for the 4HGERI reference model."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    BarTimeframe,
    EntryMaturityLevel,
    GeriAssessment,
    GeriLevelKind,
    GeriMaturity,
    GeriStructuralLevel,
    MarketBar,
    NamedValue,
    PatternDirection,
)

from .models import Swing4HGeriContext

ZERO = Decimal("0")
FOUR_PLACES = Decimal("0.0001")


class Swing4HGeriEngine:
    """Alternate horizontal support/resistance only after causal 4h close breaks."""

    engine_id = "4hgeri"
    engine_version = "1.0.0"

    def __init__(
        self,
        *,
        pivot_radius: int = 1,
        minimum_bars: int = 8,
        lookback_bars: int = 60,
        breakout_atr: Decimal = Decimal("0.10"),
        zone_atr: Decimal = Decimal("0.25"),
        invalidation_atr: Decimal = Decimal("0.50"),
    ) -> None:
        if pivot_radius < 1:
            raise ValueError("pivot radius must be positive")
        if minimum_bars < pivot_radius * 2 + 5:
            raise ValueError("minimum bars cannot establish alternating levels")
        if lookback_bars < minimum_bars:
            raise ValueError("lookback cannot be shorter than minimum bars")
        if breakout_atr <= ZERO or zone_atr <= breakout_atr:
            raise ValueError("4HGERI ATR buffers are out of order")
        if invalidation_atr <= zone_atr:
            raise ValueError("invalidation buffer must exceed the entry zone")
        self._pivot_radius = pivot_radius
        self._minimum_bars = minimum_bars
        self._lookback = lookback_bars
        self._breakout_atr = breakout_atr
        self._zone_atr = zone_atr
        self._invalidation_atr = invalidation_atr

    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment:
        symbol = context.symbol.strip().upper()
        bars = tuple(bar for bar in context.bars[-self._lookback :] if bar.is_final)
        if len(bars) < self._minimum_bars:
            raise ValueError("4HGERI requires more completed bars")
        if any(bar.symbol != symbol for bar in bars):
            raise ValueError("4HGERI bars must belong to the requested symbol")
        if any(bar.timeframe is not BarTimeframe.HOUR_4 for bar in bars):
            raise ValueError("4HGERI requires 4Hour bars")
        if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
            raise ValueError("4HGERI bars must be strictly chronological")
        if context.current_price <= ZERO:
            raise ValueError("current price must be positive")

        levels = self._levels(bars)
        return self._assessment(context, symbol=symbol, bars=bars, levels=levels)

    def _assessment(
        self,
        context: Swing4HGeriContext,
        *,
        symbol: str,
        bars: tuple[MarketBar, ...],
        levels: tuple[GeriStructuralLevel, ...],
        tracking_extreme: tuple[Decimal, datetime] | None = None,
    ) -> GeriAssessment:
        atr14 = _atr(bars)
        active = levels[-1]
        breakout_buffer = atr14 * self._breakout_atr
        daily_low, daily_high = _daily_swing_zone(context.daily_swing)
        existing_aligned = context.existing_maturity in {
            EntryMaturityLevel.L3,
            EntryMaturityLevel.L4,
        }
        zone_low: Decimal | None = None
        zone_high: Decimal | None = None
        invalidation: Decimal | None = None
        bounce = False
        daily_aligned = False
        if active.kind is GeriLevelKind.SUPPORT:
            zone_padding = atr14 * self._zone_atr
            zone_low = max(Decimal("0.0001"), active.price - zone_padding)
            zone_high = active.price + zone_padding
            invalidation = max(
                Decimal("0.0001"),
                active.price - atr14 * self._invalidation_atr,
            )
            bounce = _bounce_confirmed(
                bars,
                support=active.price,
                confirmed_at=active.confirmed_at,
                zone_high=zone_high,
                invalidation=invalidation,
            )
            daily_aligned = _daily_swing_aligned(
                context.daily_swing,
                level_low=zone_low,
                level_high=zone_high,
                swing_low=daily_low,
                swing_high=daily_high,
            )
        maturity = _maturity(
            level_count=len(levels),
            active_kind=active.kind,
            price=context.current_price,
            zone_low=zone_low,
            zone_high=zone_high,
            invalidation=invalidation,
            bounce=bounce,
            daily_aligned=daily_aligned,
            existing_aligned=existing_aligned,
        )
        return GeriAssessment(
            symbol=symbol,
            occurred_at=bars[-1].timestamp,
            engine_version=self.engine_version,
            maturity=maturity,
            current_price=_rounded(context.current_price),
            levels=levels,
            active_level_sequence=active.sequence,
            active_level_kind=active.kind,
            active_level_price=active.price,
            atr14=_rounded(atr14),
            breakout_buffer=_rounded(breakout_buffer),
            zone_low=_rounded(zone_low) if zone_low is not None else None,
            zone_high=_rounded(zone_high) if zone_high is not None else None,
            invalidation=_rounded(invalidation) if invalidation is not None else None,
            bounce_confirmed=bounce,
            daily_swing_aligned=daily_aligned,
            existing_maturity_aligned=existing_aligned,
            current_swing_zone_low=_rounded(daily_low) if daily_low is not None else None,
            current_swing_zone_high=(
                _rounded(daily_high) if daily_high is not None else None
            ),
            reasons=_reasons(maturity, active, daily_aligned, existing_aligned),
            metrics=(
                NamedValue(name="atr14_4h", value=_rounded(atr14)),
                NamedValue(name="break_confirmation", value="completed_4h_close"),
                NamedValue(name="breakout_atr", value=self._breakout_atr),
                NamedValue(name="structure", value="alternating_horizontal_levels"),
                NamedValue(name="rth_anchor", value="09:30_America/New_York"),
                *(
                    (
                        NamedValue(
                            name="tracking_extreme_price",
                            value=_rounded(tracking_extreme[0]),
                        ),
                        NamedValue(
                            name="tracking_extreme_at", value=tracking_extreme[1]
                        ),
                    )
                    if tracking_extreme is not None
                    else ()
                ),
            ),
            context_hash=_context_hash(bars, context.current_price, context.daily_swing),
        )

    def _levels(self, bars: tuple[MarketBar, ...]) -> tuple[GeriStructuralLevel, ...]:
        seed = _first_pivot_low(bars, self._pivot_radius)
        confirmed_index = seed + self._pivot_radius
        levels: list[GeriStructuralLevel] = [
            GeriStructuralLevel(
                sequence=1,
                kind=GeriLevelKind.SUPPORT,
                price=_rounded(bars[seed].low),
                source_at=bars[seed].timestamp,
                confirmed_at=bars[confirmed_index].timestamp,
            )
        ]
        tracking_start = confirmed_index
        for index in range(confirmed_index + 1, len(bars)):
            active = levels[-1]
            current = bars[index]
            causal_atr = _atr(bars[: index + 1])
            buffer = causal_atr * self._breakout_atr
            broken = (
                current.close < active.price - buffer
                if active.kind is GeriLevelKind.SUPPORT
                else current.close > active.price + buffer
            )
            if not broken:
                continue
            segment = bars[tracking_start:index]
            if not segment:
                continue
            levels[-1] = active.model_copy(update={"broken_at": current.timestamp})
            if active.kind is GeriLevelKind.SUPPORT:
                extreme = max(segment, key=lambda bar: (bar.high, bar.timestamp))
                kind = GeriLevelKind.RESISTANCE
                price = extreme.high
            else:
                extreme = min(segment, key=lambda bar: (bar.low, bar.timestamp))
                kind = GeriLevelKind.SUPPORT
                price = extreme.low
            levels.append(
                GeriStructuralLevel(
                    sequence=len(levels) + 1,
                    kind=kind,
                    price=_rounded(price),
                    source_at=extreme.timestamp,
                    confirmed_at=current.timestamp,
                )
            )
            tracking_start = index
        return tuple(levels)


class Swing4HGeriEngineV11(Swing4HGeriEngine):
    """Extend the published level chain instead of rebuilding it from a rolling window."""

    engine_version = "1.1.0"

    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment:
        active_structure = context.active_structure
        if active_structure is None:
            initial = super().analyze(context)
            bars = tuple(
                bar for bar in context.bars[-self._lookback :] if bar.is_final
            )
            extreme = _tracking_extreme(
                bars,
                initial.levels[-1],
                through=initial.occurred_at,
            )
            return self._assessment(
                context,
                symbol=context.symbol.strip().upper(),
                bars=bars,
                levels=initial.levels,
                tracking_extreme=extreme,
            )

        symbol = context.symbol.strip().upper()
        bars = tuple(bar for bar in context.bars[-self._lookback :] if bar.is_final)
        if len(bars) < self._minimum_bars:
            raise ValueError("4HGERI requires more completed bars")
        if any(bar.symbol != symbol for bar in bars):
            raise ValueError("4HGERI bars must belong to the requested symbol")
        if any(bar.timeframe is not BarTimeframe.HOUR_4 for bar in bars):
            raise ValueError("4HGERI requires 4Hour bars")
        if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
            raise ValueError("4HGERI bars must be strictly chronological")
        if context.current_price <= ZERO:
            raise ValueError("current price must be positive")
        if active_structure.symbol != symbol:
            raise ValueError("active 4HGERI structure must belong to the requested symbol")

        levels = list(active_structure.levels)
        extreme = _assessment_tracking_extreme(active_structure)
        if extreme is None:
            extreme = _tracking_extreme(
                bars,
                levels[-1],
                through=active_structure.occurred_at,
            )
        for index, current in enumerate(bars):
            if current.timestamp <= active_structure.occurred_at:
                continue
            active = levels[-1]
            causal_atr = _atr(bars[: index + 1])
            buffer = causal_atr * self._breakout_atr
            broken = (
                current.close < active.price - buffer
                if active.kind is GeriLevelKind.SUPPORT
                else current.close > active.price + buffer
            )
            if broken:
                levels[-1] = active.model_copy(update={"broken_at": current.timestamp})
                next_kind = (
                    GeriLevelKind.RESISTANCE
                    if active.kind is GeriLevelKind.SUPPORT
                    else GeriLevelKind.SUPPORT
                )
                levels.append(
                    GeriStructuralLevel(
                        sequence=len(levels) + 1,
                        kind=next_kind,
                        price=_rounded(extreme[0]),
                        source_at=extreme[1],
                        confirmed_at=current.timestamp,
                    )
                )
                extreme = _bar_extreme(current, next_kind)
                continue
            extreme = _updated_extreme(extreme, current, active.kind)

        return self._assessment(
            context,
            symbol=symbol,
            bars=bars,
            levels=tuple(levels),
            tracking_extreme=extreme,
        )


def _first_pivot_low(bars: tuple[MarketBar, ...], radius: int) -> int:
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        low = bars[index].low
        if all(low <= bar.low for bar in neighbors) and any(low < bar.low for bar in neighbors):
            return index
    raise ValueError("4HGERI has no confirmed initial pivot low")


def _assessment_tracking_extreme(
    assessment: GeriAssessment,
) -> tuple[Decimal, datetime] | None:
    metrics = {item.name: item.value for item in assessment.metrics}
    price = metrics.get("tracking_extreme_price")
    occurred_at = metrics.get("tracking_extreme_at")
    if isinstance(price, Decimal) and isinstance(occurred_at, datetime):
        return price, occurred_at
    return None


def _tracking_extreme(
    bars: tuple[MarketBar, ...],
    active: GeriStructuralLevel,
    *,
    through: datetime,
) -> tuple[Decimal, datetime]:
    segment = tuple(
        bar
        for bar in bars
        if active.confirmed_at <= bar.timestamp <= through
    )
    if not segment:
        return active.price, active.source_at
    if active.kind is GeriLevelKind.SUPPORT:
        extreme = max(segment, key=lambda bar: (bar.high, bar.timestamp))
        return extreme.high, extreme.timestamp
    extreme = min(segment, key=lambda bar: (bar.low, bar.timestamp))
    return extreme.low, extreme.timestamp


def _bar_extreme(bar: MarketBar, kind: GeriLevelKind) -> tuple[Decimal, datetime]:
    return (
        (bar.high, bar.timestamp)
        if kind is GeriLevelKind.SUPPORT
        else (bar.low, bar.timestamp)
    )


def _updated_extreme(
    current: tuple[Decimal, datetime],
    bar: MarketBar,
    kind: GeriLevelKind,
) -> tuple[Decimal, datetime]:
    candidate = _bar_extreme(bar, kind)
    if kind is GeriLevelKind.SUPPORT:
        return max((current, candidate), key=lambda item: (item[0], item[1]))
    return min((current, candidate), key=lambda item: (item[0], item[1]))


def _bounce_confirmed(
    bars: tuple[MarketBar, ...],
    *,
    support: Decimal,
    confirmed_at: datetime,
    zone_high: Decimal,
    invalidation: Decimal,
) -> bool:
    if len(bars) < 2:
        return False
    touched = bars[-2]
    current = bars[-1]
    return (
        touched.timestamp >= confirmed_at
        and touched.low <= zone_high
        and touched.high >= invalidation
        and touched.close >= invalidation
        and current.low > touched.low
        and current.close > current.open
        and current.close > touched.close
        and current.close > support
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
    level_low: Decimal,
    level_high: Decimal,
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
        and swing_low <= level_high
        and swing_high >= level_low
    )


def _maturity(
    *,
    level_count: int,
    active_kind: GeriLevelKind,
    price: Decimal,
    zone_low: Decimal | None,
    zone_high: Decimal | None,
    invalidation: Decimal | None,
    bounce: bool,
    daily_aligned: bool,
    existing_aligned: bool,
) -> GeriMaturity:
    if active_kind is GeriLevelKind.RESISTANCE or level_count < 3:
        return GeriMaturity.BUILDING
    assert zone_low is not None and zone_high is not None and invalidation is not None
    if price <= invalidation:
        return GeriMaturity.INVALIDATED
    if bounce and daily_aligned and existing_aligned:
        return GeriMaturity.L4
    if bounce and daily_aligned:
        return GeriMaturity.L3
    if bounce:
        return GeriMaturity.L2_4H
    if zone_low <= price <= zone_high:
        return GeriMaturity.IN_ZONE_4H
    return GeriMaturity.ARMED


def _reasons(
    maturity: GeriMaturity,
    active: GeriStructuralLevel,
    daily_aligned: bool,
    existing_aligned: bool,
) -> tuple[str, ...]:
    reasons = {
        GeriMaturity.BUILDING: ["building_alternating_structure"],
        GeriMaturity.ARMED: ["confirmed_horizontal_support_armed"],
        GeriMaturity.IN_ZONE_4H: ["horizontal_support_retest"],
        GeriMaturity.L2_4H: ["four_hour_higher_low_bounce"],
        GeriMaturity.L3: ["four_hour_bounce_aligned_with_daily_swing"],
        GeriMaturity.L4: ["four_hour_and_existing_maturity_aligned"],
        GeriMaturity.INVALIDATED: ["live_support_invalidation_breached"],
    }[maturity]
    reasons.append(f"active_level:{active.sequence}:{active.kind.value}")
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
        raise ValueError("4HGERI ATR requires multiple bars")
    value = sum(sample, ZERO) / Decimal(len(sample))
    if value <= ZERO:
        raise ValueError("4HGERI ATR must be positive")
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
