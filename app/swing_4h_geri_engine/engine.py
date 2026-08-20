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
    TradeSide,
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
            current_swing_zone_high=(_rounded(daily_high) if daily_high is not None else None),
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
                        NamedValue(name="tracking_extreme_at", value=tracking_extreme[1]),
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
            bars = tuple(bar for bar in context.bars[-self._lookback :] if bar.is_final)
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


class Swing4HGeriEngineV12(Swing4HGeriEngine):
    """Standalone mirrored Swing model for manual G0-G4 monitoring only."""

    engine_version = "1.2.0"

    def __init__(
        self,
        *,
        pivot_radius: int = 1,
        minimum_bars: int = 8,
        lookback_bars: int = 60,
        breakout_atr: Decimal = Decimal("0.10"),
        zone_atr: Decimal = Decimal("0.25"),
        invalidation_atr: Decimal = Decimal("0.50"),
        maximum_extension_atr: Decimal = Decimal("1.50"),
    ) -> None:
        super().__init__(
            pivot_radius=pivot_radius,
            minimum_bars=minimum_bars,
            lookback_bars=lookback_bars,
            breakout_atr=breakout_atr,
            zone_atr=zone_atr,
            invalidation_atr=invalidation_atr,
        )
        if maximum_extension_atr <= ZERO:
            raise ValueError("maximum extension ATR must be positive")
        self._maximum_extension_atr = maximum_extension_atr

    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment:
        symbol = context.symbol.strip().upper()
        bars = tuple(bar for bar in context.bars[-self._lookback :] if bar.is_final)
        _validate_bars(symbol, bars, minimum_bars=self._minimum_bars)
        if context.current_price <= ZERO:
            raise ValueError("current price must be positive")
        confirmations = tuple(bar for bar in context.confirmation_bars if bar.is_final)
        if any(bar.symbol != symbol for bar in confirmations):
            raise ValueError("4HGERI confirmation bars must belong to the symbol")
        if any(bar.timeframe is not BarTimeframe.MINUTE_15 for bar in confirmations):
            raise ValueError("4HGERI fast confirmation requires 15Min bars")
        if any(
            current.timestamp <= previous.timestamp for previous, current in pairwise(confirmations)
        ):
            raise ValueError("4HGERI confirmation bars must be chronological")

        active_structure = context.active_structure
        if (
            active_structure is not None
            and active_structure.engine_version == self.engine_version
            and active_structure.standalone_swing
        ):
            if active_structure.symbol != symbol:
                raise ValueError("active 4HGERI structure must belong to the symbol")
            levels, tracking_extreme = self._extend_active_levels(
                bars,
                active_structure,
            )
            return self._standalone_assessment(
                context,
                symbol=symbol,
                bars=bars,
                confirmation_bars=confirmations,
                levels=levels,
                side=active_structure.trade_side,
                tracking_extreme=tracking_extreme,
            )

        candidates: list[tuple[TradeSide, tuple[GeriStructuralLevel, ...]]] = []
        for side, seed_kind in (
            (TradeSide.LONG, GeriLevelKind.SUPPORT),
            (TradeSide.SHORT, GeriLevelKind.RESISTANCE),
        ):
            try:
                candidates.append((side, self._levels_from_seed(bars, seed_kind)))
            except ValueError:
                continue
        if not candidates:
            raise ValueError("4HGERI has no confirmed initial pivot")
        side, levels = max(candidates, key=_candidate_priority)
        tracking_extreme = _tracking_extreme(
            bars,
            levels[-1],
            through=bars[-1].timestamp,
        )
        return self._standalone_assessment(
            context,
            symbol=symbol,
            bars=bars,
            confirmation_bars=confirmations,
            levels=levels,
            side=side,
            tracking_extreme=tracking_extreme,
        )

    def _extend_active_levels(
        self,
        bars: tuple[MarketBar, ...],
        active_structure: GeriAssessment,
    ) -> tuple[tuple[GeriStructuralLevel, ...], tuple[Decimal, datetime]]:
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
            buffer = _atr(bars[: index + 1]) * self._breakout_atr
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
        return tuple(levels), extreme

    def _levels_from_seed(
        self,
        bars: tuple[MarketBar, ...],
        seed_kind: GeriLevelKind,
    ) -> tuple[GeriStructuralLevel, ...]:
        seed = (
            _first_pivot_low(bars, self._pivot_radius)
            if seed_kind is GeriLevelKind.SUPPORT
            else _first_pivot_high(bars, self._pivot_radius)
        )
        confirmed_index = seed + self._pivot_radius
        seed_price = bars[seed].low if seed_kind is GeriLevelKind.SUPPORT else bars[seed].high
        levels: list[GeriStructuralLevel] = [
            GeriStructuralLevel(
                sequence=1,
                kind=seed_kind,
                price=_rounded(seed_price),
                source_at=bars[seed].timestamp,
                confirmed_at=bars[confirmed_index].timestamp,
            )
        ]
        tracking_start = confirmed_index
        for index in range(confirmed_index + 1, len(bars)):
            active = levels[-1]
            current = bars[index]
            buffer = _atr(bars[: index + 1]) * self._breakout_atr
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
                next_kind = GeriLevelKind.RESISTANCE
                next_price = extreme.high
            else:
                extreme = min(segment, key=lambda bar: (bar.low, bar.timestamp))
                next_kind = GeriLevelKind.SUPPORT
                next_price = extreme.low
            levels.append(
                GeriStructuralLevel(
                    sequence=len(levels) + 1,
                    kind=next_kind,
                    price=_rounded(next_price),
                    source_at=extreme.timestamp,
                    confirmed_at=current.timestamp,
                )
            )
            tracking_start = index
        return tuple(levels)

    def _standalone_assessment(
        self,
        context: Swing4HGeriContext,
        *,
        symbol: str,
        bars: tuple[MarketBar, ...],
        confirmation_bars: tuple[MarketBar, ...],
        levels: tuple[GeriStructuralLevel, ...],
        side: TradeSide,
        tracking_extreme: tuple[Decimal, datetime],
    ) -> GeriAssessment:
        active = levels[-1]
        atr14 = _atr(bars)
        expected_kind = (
            GeriLevelKind.SUPPORT if side is TradeSide.LONG else GeriLevelKind.RESISTANCE
        )
        actionable = len(levels) >= 3 and active.kind is expected_kind
        zone_low: Decimal | None = None
        zone_high: Decimal | None = None
        invalidation: Decimal | None = None
        fast = False
        four_hour = False
        continuation = False
        if actionable:
            padding = atr14 * self._zone_atr
            zone_low = max(Decimal("0.0001"), active.price - padding)
            zone_high = active.price + padding
            invalidation = (
                max(
                    Decimal("0.0001"),
                    active.price - atr14 * self._invalidation_atr,
                )
                if side is TradeSide.LONG
                else active.price + atr14 * self._invalidation_atr
            )
            fast = _fast_rejection_confirmed(
                confirmation_bars,
                side=side,
                level=active.price,
                zone_low=zone_low,
                zone_high=zone_high,
                confirmed_at=active.confirmed_at,
            )
            four_hour = _four_hour_reaction_confirmed(
                bars,
                side=side,
                level=active.price,
                zone_low=zone_low,
                zone_high=zone_high,
                confirmed_at=active.confirmed_at,
            )
            continuation = _continuation_confirmed(
                confirmation_bars,
                side=side,
                four_hour_confirmed=four_hour,
            )
        maturity = _standalone_maturity(
            actionable=actionable,
            side=side,
            price=context.current_price,
            active_price=active.price,
            atr14=atr14,
            maximum_extension_atr=self._maximum_extension_atr,
            zone_low=zone_low,
            zone_high=zone_high,
            invalidation=invalidation,
            fast=fast,
            four_hour=four_hour,
            continuation=continuation,
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
            breakout_buffer=_rounded(atr14 * self._breakout_atr),
            zone_low=_rounded(zone_low) if zone_low is not None else None,
            zone_high=_rounded(zone_high) if zone_high is not None else None,
            invalidation=_rounded(invalidation) if invalidation is not None else None,
            bounce_confirmed=fast or four_hour,
            trade_side=side,
            standalone_swing=True,
            fast_confirmation=fast,
            four_hour_confirmation=four_hour,
            continuation_confirmation=continuation,
            reasons=_standalone_reasons(maturity, active, side),
            metrics=(
                NamedValue(name="atr14_4h", value=_rounded(atr14)),
                NamedValue(name="break_confirmation", value="completed_4h_close"),
                NamedValue(name="structure", value="mirrored_alternating_levels"),
                NamedValue(name="entry_lifecycle", value="manual_g0_g4"),
                NamedValue(name="emits_opportunities", value=False),
                NamedValue(name="places_orders", value=False),
                NamedValue(
                    name="tracking_extreme_price",
                    value=_rounded(tracking_extreme[0]),
                ),
                NamedValue(name="tracking_extreme_at", value=tracking_extreme[1]),
            ),
            context_hash=_context_hash(
                bars,
                context.current_price,
                None,
                confirmation_bars=confirmation_bars,
            ),
        )


def _first_pivot_low(bars: tuple[MarketBar, ...], radius: int) -> int:
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        low = bars[index].low
        if all(low <= bar.low for bar in neighbors) and any(low < bar.low for bar in neighbors):
            return index
    raise ValueError("4HGERI has no confirmed initial pivot low")


def _first_pivot_high(bars: tuple[MarketBar, ...], radius: int) -> int:
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        high = bars[index].high
        if all(high >= bar.high for bar in neighbors) and any(high > bar.high for bar in neighbors):
            return index
    raise ValueError("4HGERI has no confirmed initial pivot high")


def _validate_bars(
    symbol: str,
    bars: tuple[MarketBar, ...],
    *,
    minimum_bars: int,
) -> None:
    if len(bars) < minimum_bars:
        raise ValueError("4HGERI requires more completed bars")
    if any(bar.symbol != symbol for bar in bars):
        raise ValueError("4HGERI bars must belong to the requested symbol")
    if any(bar.timeframe is not BarTimeframe.HOUR_4 for bar in bars):
        raise ValueError("4HGERI requires 4Hour bars")
    if any(current.timestamp <= previous.timestamp for previous, current in pairwise(bars)):
        raise ValueError("4HGERI bars must be strictly chronological")


def _candidate_priority(
    candidate: tuple[TradeSide, tuple[GeriStructuralLevel, ...]],
) -> tuple[bool, datetime, int]:
    side, levels = candidate
    if not levels:
        raise ValueError("4HGERI candidate cannot be empty")
    latest = max(levels, key=lambda level: level.sequence)
    expected = GeriLevelKind.SUPPORT if side is TradeSide.LONG else GeriLevelKind.RESISTANCE
    actionable = len(levels) >= 3 and latest.kind is expected
    return actionable, latest.confirmed_at, len(levels)


def _fast_rejection_confirmed(
    bars: tuple[MarketBar, ...],
    *,
    side: TradeSide,
    level: Decimal,
    zone_low: Decimal,
    zone_high: Decimal,
    confirmed_at: datetime,
) -> bool:
    if len(bars) < 2:
        return False
    touched, current = bars[-2:]
    if touched.timestamp < confirmed_at:
        return False
    touched_zone = touched.low <= zone_high and touched.high >= zone_low
    if side is TradeSide.LONG:
        return bool(
            touched_zone
            and current.low > touched.low
            and current.close > current.open
            and current.close > touched.close
            and current.close > level
        )
    return bool(
        touched_zone
        and current.high < touched.high
        and current.close < current.open
        and current.close < touched.close
        and current.close < level
    )


def _four_hour_reaction_confirmed(
    bars: tuple[MarketBar, ...],
    *,
    side: TradeSide,
    level: Decimal,
    zone_low: Decimal,
    zone_high: Decimal,
    confirmed_at: datetime,
) -> bool:
    reactions = tuple(bar for bar in bars if bar.timestamp >= confirmed_at)
    if len(reactions) < 2:
        return False
    touched, current = reactions[-2:]
    touched_zone = touched.low <= zone_high and touched.high >= zone_low
    if side is TradeSide.LONG:
        return bool(
            touched_zone
            and current.low > touched.low
            and current.close > touched.high
            and current.close > level
        )
    return bool(
        touched_zone
        and current.high < touched.high
        and current.close < touched.low
        and current.close < level
    )


def _continuation_confirmed(
    bars: tuple[MarketBar, ...],
    *,
    side: TradeSide,
    four_hour_confirmed: bool,
) -> bool:
    if not four_hour_confirmed or len(bars) < 3:
        return False
    breakout, retest, current = bars[-3:]
    if side is TradeSide.LONG:
        return bool(
            breakout.close > breakout.open
            and retest.low <= breakout.high
            and current.close > breakout.high
        )
    return bool(
        breakout.close < breakout.open
        and retest.high >= breakout.low
        and current.close < breakout.low
    )


def _standalone_maturity(
    *,
    actionable: bool,
    side: TradeSide,
    price: Decimal,
    active_price: Decimal,
    atr14: Decimal,
    maximum_extension_atr: Decimal,
    zone_low: Decimal | None,
    zone_high: Decimal | None,
    invalidation: Decimal | None,
    fast: bool,
    four_hour: bool,
    continuation: bool,
) -> GeriMaturity:
    if not actionable:
        return GeriMaturity.BUILDING
    assert zone_low is not None and zone_high is not None and invalidation is not None
    if side is TradeSide.LONG:
        if price <= invalidation:
            return GeriMaturity.INVALIDATED
        if price < zone_low:
            return GeriMaturity.RECLAIM_REQUIRED
        extension = price - active_price
    else:
        if price >= invalidation:
            return GeriMaturity.INVALIDATED
        if price > zone_high:
            return GeriMaturity.RECLAIM_REQUIRED
        extension = active_price - price
    if extension > atr14 * maximum_extension_atr:
        return GeriMaturity.EXTENDED
    if continuation:
        return GeriMaturity.L4
    if four_hour:
        return GeriMaturity.L3
    if fast:
        return GeriMaturity.L2_4H
    if zone_low <= price <= zone_high:
        return GeriMaturity.IN_ZONE_4H
    return GeriMaturity.ARMED


def _standalone_reasons(
    maturity: GeriMaturity,
    active: GeriStructuralLevel,
    side: TradeSide,
) -> tuple[str, ...]:
    stage = {
        GeriMaturity.BUILDING: "g0_structure_building",
        GeriMaturity.ARMED: "g0_level_armed",
        GeriMaturity.IN_ZONE_4H: "g1_price_in_level_zone",
        GeriMaturity.L2_4H: "g2_fast_rejection_confirmed",
        GeriMaturity.L3: "g3_completed_4h_reaction",
        GeriMaturity.L4: "g4_breakout_retest_continuation",
        GeriMaturity.EXTENDED: "impulse_extended_awaiting_pullback",
        GeriMaturity.RECLAIM_REQUIRED: "broken_level_reclaim_required",
        GeriMaturity.INVALIDATED: "level_invalidation_breached",
    }[maturity]
    return (
        "manual_monitor_only",
        stage,
        f"trade_side:{side.value}",
        f"active_level:{active.sequence}:{active.kind.value}",
    )


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
    segment = tuple(bar for bar in bars if active.confirmed_at <= bar.timestamp <= through)
    if not segment:
        return active.price, active.source_at
    if active.kind is GeriLevelKind.SUPPORT:
        extreme = max(segment, key=lambda bar: (bar.high, bar.timestamp))
        return extreme.high, extreme.timestamp
    extreme = min(segment, key=lambda bar: (bar.low, bar.timestamp))
    return extreme.low, extreme.timestamp


def _bar_extreme(bar: MarketBar, kind: GeriLevelKind) -> tuple[Decimal, datetime]:
    return (bar.high, bar.timestamp) if kind is GeriLevelKind.SUPPORT else (bar.low, bar.timestamp)


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
    bars: tuple[MarketBar, ...],
    price: Decimal,
    daily_swing: AnalysisResult | None,
    *,
    confirmation_bars: tuple[MarketBar, ...] = (),
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
        "confirmation_bars": [
            [
                bar.timestamp.isoformat(),
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
            ]
            for bar in confirmation_bars
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"sha256:{digest}"


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
