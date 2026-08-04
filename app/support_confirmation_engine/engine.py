"""Deterministic support-reaction and structural-reversal assessment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import (
    MarketBar,
    NamedValue,
    StructuralSupportReference,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
)

from .models import SupportContext, SupportZoneHint

ZERO = Decimal()
HUNDRED = Decimal("100")
FOUR_PLACES = Decimal("0.0001")
MIN_IMPULSE_PERCENT = Decimal("15")
MIN_IMPULSE_ATR_MULTIPLE = Decimal("4")
IMPULSE_LOOKBACK_BARS = 80


@dataclass(frozen=True, slots=True)
class _Level:
    value: Decimal
    source: str
    points: Decimal


@dataclass(frozen=True, slots=True)
class _ImpulseReference:
    origin: Decimal
    origin_at: datetime
    peak: Decimal
    advance_percent: Decimal


class SupportConfirmationEngine:
    """Keep reaction evidence separate from confirmation of a new uptrend."""

    engine_id = "support-confirmation"
    engine_version = "0.2.0"

    def evaluate(self, context: SupportContext) -> SupportAssessment:
        daily = tuple(bar for bar in context.daily_bars if bar.is_final)
        weekly = tuple(bar for bar in context.weekly_bars if bar.is_final)
        hourly = tuple(bar for bar in context.hourly_bars if bar.is_final)
        if len(daily) < 15:
            raise ValueError("Support Confirmation requires 15 completed daily bars")
        atr14 = _atr(daily)
        current_price = daily[-1].close
        structural_supports = _structural_supports(
            _support_levels(daily, weekly, atr14), current_price, atr14
        )
        impulse = _recent_impulse(daily, atr14)
        zone = context.zone_hint or _best_zone(daily, weekly, atr14)
        if zone is None:
            return SupportAssessment(
                symbol=context.symbol.strip().upper(),
                occurred_at=daily[-1].timestamp,
                engine_version=self.engine_version,
                state=SupportState.NO_NEARBY_SUPPORT,
                current_price=current_price,
                support_score=ZERO,
                reaction_score=ZERO,
                reversal_score=ZERO,
                confidence=ZERO,
                structural_supports=structural_supports,
                impulse_origin=impulse.origin if impulse is not None else None,
                impulse_origin_at=impulse.origin_at if impulse is not None else None,
                impulse_peak=impulse.peak if impulse is not None else None,
                impulse_advance_percent=(
                    impulse.advance_percent if impulse is not None else None
                ),
                reasons=("no_nearby_higher_timeframe_support",),
                context_hash=_context_hash(daily, weekly, hourly),
            )

        features = _features(daily, zone, atr14)
        reaction_score = _reaction_score(zone.score, features)
        reversal_score = _reversal_score(daily, reaction_score, features)
        b_wave_risk = reaction_score >= Decimal("60") and reversal_score < Decimal("60")
        state, confirmation_type, reasons = _state(
            context.previous_assessment,
            daily[-1].close,
            zone,
            reaction_score,
            reversal_score,
            features,
        )
        confidence = max(zone.score, reaction_score, reversal_score) / HUNDRED
        return SupportAssessment(
            symbol=context.symbol.strip().upper(),
            occurred_at=daily[-1].timestamp,
            engine_version=self.engine_version,
            state=state,
            confirmation_type=confirmation_type,
            current_price=daily[-1].close,
            zone_low=_rounded(zone.low),
            zone_center=_rounded(zone.center),
            zone_high=_rounded(zone.high),
            invalidation=_rounded(zone.invalidation),
            support_score=_rounded(zone.score),
            reaction_score=_rounded(reaction_score),
            reversal_score=_rounded(reversal_score),
            confidence=_rounded(min(Decimal("1"), confidence)),
            liquidity_sweep=features.liquidity_sweep,
            higher_high=features.higher_high,
            higher_low=features.higher_low,
            b_wave_risk=b_wave_risk,
            support_sources=zone.sources,
            structural_supports=structural_supports,
            impulse_origin=impulse.origin if impulse is not None else None,
            impulse_origin_at=impulse.origin_at if impulse is not None else None,
            impulse_peak=impulse.peak if impulse is not None else None,
            impulse_advance_percent=(impulse.advance_percent if impulse is not None else None),
            reasons=reasons,
            metrics=(
                NamedValue(name="atr14_daily", value=_rounded(atr14)),
                NamedValue(name="reaction_rvol", value=_rounded(features.max_recent_rvol)),
                NamedValue(name="pre_touch_high", value=_rounded(features.pre_touch_high)),
                NamedValue(name="base_building", value=features.base_building),
                NamedValue(name="base_breakout", value=features.base_breakout),
            ),
            context_hash=_context_hash(daily, weekly, hourly),
        )


@dataclass(frozen=True, slots=True)
class _Features:
    touched: bool
    reclaimed: bool
    liquidity_sweep: bool
    v_recovery: bool
    base_building: bool
    base_breakout: bool
    bullish_candle: bool
    higher_high: bool
    higher_low: bool
    max_recent_rvol: Decimal
    pre_touch_high: Decimal


def _features(
    bars: tuple[MarketBar, ...], zone: SupportZoneHint, atr14: Decimal
) -> _Features:
    recent_start = max(0, len(bars) - 12)
    recent = bars[recent_start:]
    padding = atr14 * Decimal("0.15")
    touch_indices = tuple(
        index
        for index in range(recent_start, len(bars))
        if bars[index].low <= zone.high + padding
        and bars[index].high >= zone.low - padding
    )
    touch_index = touch_indices[0] if touch_indices else len(bars) - 1
    pre_touch = bars[max(0, touch_index - 12) : touch_index]
    pre_touch_high = max((bar.high for bar in pre_touch), default=zone.high)
    sweep_indices = tuple(
        index
        for index in range(recent_start, len(bars))
        if bars[index].low < zone.low and bars[index].close > zone.center
    )
    sweep_index = sweep_indices[-1] if sweep_indices else None
    reclaimed = bars[-1].close >= zone.high
    rvols = tuple(
        _relative_volume(bars[: index + 1])
        for index in range(max(0, len(bars) - 4), len(bars))
    )
    max_recent_rvol = max(rvols, default=ZERO)
    correction_low = min(bar.low for bar in recent)
    recent_high = max(bar.high for bar in recent)
    v_recovery = (
        reclaimed
        and recent_high - correction_low >= atr14 * Decimal("2")
        and bars[-1].close - correction_low >= atr14
        and max_recent_rvol >= Decimal("1.3")
    )
    base = bars[-8:]
    base_building = (
        len(base) >= 8
        and sum(bar.close >= zone.center for bar in base) >= 6
        and max(bar.high for bar in base) - min(bar.low for bar in base)
        <= atr14 * Decimal("2.5")
    )
    prior_base = bars[-9:-1]
    base_breakout = (
        base_building
        and bool(prior_base)
        and bars[-1].close > max(bar.high for bar in prior_base)
        and _relative_volume(bars) >= Decimal("1.2")
    )
    higher_high = bars[-1].close > pre_touch_high
    post_sweep = bars[sweep_index + 1 :] if sweep_index is not None else ()
    higher_low = (
        len(post_sweep) >= 2
        and min(bar.low for bar in post_sweep) > zone.low
        and bars[-1].close > min(bar.low for bar in post_sweep)
    )
    return _Features(
        touched=bool(touch_indices),
        reclaimed=reclaimed,
        liquidity_sweep=sweep_index is not None,
        v_recovery=v_recovery,
        base_building=base_building,
        base_breakout=base_breakout,
        bullish_candle=_bullish_candle(bars[-1]),
        higher_high=higher_high,
        higher_low=higher_low,
        max_recent_rvol=max_recent_rvol,
        pre_touch_high=pre_touch_high,
    )


def _reaction_score(support_score: Decimal, features: _Features) -> Decimal:
    score = support_score * Decimal("0.40")
    if features.reclaimed:
        score += Decimal("25")
    if features.liquidity_sweep:
        score += Decimal("20")
    if features.max_recent_rvol >= Decimal("1.5"):
        score += Decimal("15")
    elif features.max_recent_rvol >= Decimal("1.2"):
        score += Decimal("8")
    if features.bullish_candle:
        score += Decimal("8")
    if features.v_recovery:
        score += Decimal("10")
    return min(HUNDRED, score)


def _reversal_score(
    bars: tuple[MarketBar, ...], reaction_score: Decimal, features: _Features
) -> Decimal:
    score = Decimal("10") if reaction_score >= Decimal("60") else ZERO
    if features.higher_high:
        score += Decimal("35")
    if features.higher_low:
        score += Decimal("25")
    if features.base_breakout:
        score += Decimal("15")
    if len(bars) >= 50 and bars[-1].close > _sma(tuple(bar.close for bar in bars), 50):
        score += Decimal("15")
    return min(HUNDRED, score)


def _state(
    previous: SupportAssessment | None,
    price: Decimal,
    zone: SupportZoneHint,
    reaction_score: Decimal,
    reversal_score: Decimal,
    features: _Features,
) -> tuple[SupportState, SupportConfirmationType, tuple[str, ...]]:
    if price < zone.invalidation:
        return SupportState.INVALIDATED, SupportConfirmationType.NONE, (
            "daily_close_below_structural_invalidation",
        )
    if features.higher_high and features.higher_low and reversal_score >= Decimal("60"):
        confirmation = (
            SupportConfirmationType.SWEEP_RECLAIM
            if features.liquidity_sweep
            else SupportConfirmationType.BASE_BREAKOUT
        )
        return SupportState.STRUCTURE_CONFIRMED, confirmation, (
            "reaction_followed_by_higher_high",
            "higher_low_holds_above_support",
        )
    if (
        previous is not None
        and previous.state in {SupportState.REACTION_CONFIRMED, SupportState.RECLAIMED}
        and price < zone.center
        and reversal_score < Decimal("60")
    ):
        return SupportState.B_WAVE_RISK, previous.confirmation_type, (
            "reaction_rejected_without_structural_reversal",
        )
    if features.liquidity_sweep and features.reclaimed:
        return SupportState.RECLAIMED, SupportConfirmationType.SWEEP_RECLAIM, (
            "liquidity_sweep_below_support",
            "support_reclaimed_on_close",
            "reaction_not_yet_trend_reversal",
        )
    if features.liquidity_sweep:
        return SupportState.LIQUIDITY_SWEEP, SupportConfirmationType.SWEEP_RECLAIM, (
            "liquidity_sweep_waiting_for_reclaim",
        )
    if features.v_recovery and reaction_score >= Decimal("60"):
        return SupportState.REACTION_CONFIRMED, SupportConfirmationType.V_RECOVERY, (
            "capitulation_v_recovery",
            "reaction_not_yet_trend_reversal",
        )
    if features.base_breakout and reaction_score >= Decimal("60"):
        return SupportState.REACTION_CONFIRMED, SupportConfirmationType.BASE_BREAKOUT, (
            "base_breakout_with_volume",
            "reaction_not_yet_trend_reversal",
        )
    if features.base_building:
        return SupportState.BASE_BUILDING, SupportConfirmationType.BASE_BREAKOUT, (
            "consolidation_above_key_support",
            "base_requires_breakout_confirmation",
        )
    if features.touched:
        return SupportState.FIRST_TOUCH, SupportConfirmationType.NONE, (
            "key_support_first_touch",
        )
    return SupportState.WATCH_KEY_SUPPORT, SupportConfirmationType.NONE, (
        "price_approaching_key_support",
    )


def _best_zone(
    daily: tuple[MarketBar, ...], weekly: tuple[MarketBar, ...], atr14: Decimal
) -> SupportZoneHint | None:
    levels = list(_support_levels(daily, weekly, atr14))
    if not levels:
        return None
    price = daily[-1].close
    candidates: list[SupportZoneHint] = []
    for seed in levels:
        cluster = tuple(
            level
            for level in levels
            if abs(level.value - seed.value) <= atr14 * Decimal("0.35")
        )
        sources = tuple(dict.fromkeys(level.source for level in cluster))
        if len(sources) < 2:
            continue
        center = sum((level.value for level in cluster), ZERO) / Decimal(len(cluster))
        low = min(level.value for level in cluster) - atr14 * Decimal("0.10")
        high = max(level.value for level in cluster) + atr14 * Decimal("0.10")
        if not low < price <= high + atr14 * Decimal("1.5"):
            continue
        defenses = sum(
            abs(bar.low - center) <= atr14 * Decimal("0.25") and bar.close > center
            for bar in daily[-120:]
        )
        score = min(
            HUNDRED,
            sum((level.points for level in cluster), ZERO)
            + Decimal("8") * Decimal(min(3, defenses)),
        )
        candidates.append(
            SupportZoneHint(
                low=_rounded(low),
                center=_rounded(center),
                high=_rounded(high),
                invalidation=_rounded(low - atr14 * Decimal("0.75")),
                score=_rounded(score),
                sources=sources,
            )
        )
    return max(
        candidates,
        key=lambda item: (item.score, -abs(price - item.center)),
        default=None,
    )


def _support_levels(
    daily: tuple[MarketBar, ...], weekly: tuple[MarketBar, ...], atr14: Decimal
) -> tuple[_Level, ...]:
    output: list[_Level] = []
    daily_lows, daily_highs = _confirmed_pivots(daily)
    weekly_lows, _ = _confirmed_pivots(weekly) if len(weekly) >= 5 else ((), ())
    for index in daily_lows[-3:]:
        output.append(_Level(daily[index].low, f"pivot_daily_{index}", Decimal("20")))
    for index in weekly_lows[-3:]:
        output.append(_Level(weekly[index].low, f"pivot_weekly_{index}", Decimal("25")))
    daily_closes = tuple(bar.close for bar in daily)
    weekly_closes = tuple(bar.close for bar in weekly)
    for period in (50, 200):
        if len(daily_closes) >= period:
            output.append(_Level(_sma(daily_closes, period), f"daily_sma{period}", Decimal("15")))
    for period in (10, 30, 50, 200):
        if len(weekly_closes) >= period:
            output.append(_Level(_sma(weekly_closes, period), f"weekly_sma{period}", Decimal("20")))
    if daily_lows and daily_highs:
        high_index = daily_highs[-1]
        origins = tuple(index for index in daily_lows if index < high_index)
        if origins:
            origin = daily[origins[-1]].low
            peak = daily[high_index].high
            impulse = peak - origin
            if impulse >= atr14 * Decimal("2"):
                for name, ratio in (
                    ("fib_0500", Decimal("0.500")),
                    ("fib_0618", Decimal("0.618")),
                    ("fib_0786", Decimal("0.786")),
                ):
                    output.append(_Level(peak - impulse * ratio, name, Decimal("15")))
    increment = (
        Decimal("1")
        if daily[-1].close < Decimal("50")
        else Decimal("5")
        if daily[-1].close <= Decimal("200")
        else Decimal("10")
    )
    round_level = (daily[-1].close / increment).quantize(Decimal("1")) * increment
    output.append(_Level(round_level, "round_number", Decimal("5")))
    return tuple(output)


def _structural_supports(
    levels: tuple[_Level, ...], price: Decimal, atr14: Decimal
) -> tuple[StructuralSupportReference, ...]:
    below = sorted(
        (
            level
            for level in levels
            if level.value < price and level.source != "round_number"
        ),
        key=lambda item: item.value,
        reverse=True,
    )
    selected = list(below[:3])
    weekly_sma200 = next(
        (level for level in below if level.source == "weekly_sma200"), None
    )
    if weekly_sma200 is not None and all(
        item.source != weekly_sma200.source for item in selected
    ):
        selected.append(weekly_sma200)
    return tuple(
        StructuralSupportReference(
            source=level.source,
            price=_rounded(level.value),
            distance_percent=_rounded((price - level.value) / price * HUNDRED),
            distance_atr=_rounded((price - level.value) / atr14),
        )
        for level in selected
    )


def _recent_impulse(
    daily: tuple[MarketBar, ...], atr14: Decimal
) -> _ImpulseReference | None:
    pivot_lows, _ = _confirmed_pivots(daily)
    minimum_index = max(0, len(daily) - IMPULSE_LOOKBACK_BARS)
    for index in reversed(pivot_lows):
        if index < minimum_index:
            break
        origin = daily[index].low
        if origin >= daily[-1].close:
            continue
        peak = max(bar.high for bar in daily[index:])
        advance = peak - origin
        advance_percent = advance / origin * HUNDRED
        if (
            advance_percent >= MIN_IMPULSE_PERCENT
            and advance >= atr14 * MIN_IMPULSE_ATR_MULTIPLE
        ):
            return _ImpulseReference(
                origin=_rounded(origin),
                origin_at=daily[index].timestamp,
                peak=_rounded(peak),
                advance_percent=_rounded(advance_percent),
            )
    return None


def _confirmed_pivots(
    bars: tuple[MarketBar, ...], radius: int = 2
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    lows: list[int] = []
    highs: list[int] = []
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        if all(bars[index].low <= bar.low for bar in neighbors) and any(
            bars[index].low < bar.low for bar in neighbors
        ):
            lows.append(index)
        if all(bars[index].high >= bar.high for bar in neighbors) and any(
            bars[index].high > bar.high for bar in neighbors
        ):
            highs.append(index)
    return tuple(lows), tuple(highs)


def _atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    selected = bars[-(period + 1) :]
    ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(selected)
    )
    return sum(ranges, ZERO) / Decimal(len(ranges))


def _relative_volume(bars: tuple[MarketBar, ...], period: int = 20) -> Decimal:
    if len(bars) < 2:
        return ZERO
    baseline_bars = bars[max(0, len(bars) - period - 1) : -1]
    baseline = sum((bar.volume for bar in baseline_bars), ZERO) / Decimal(len(baseline_bars))
    return ZERO if baseline <= ZERO else bars[-1].volume / baseline


def _sma(values: tuple[Decimal, ...], period: int) -> Decimal:
    return sum(values[-period:], ZERO) / Decimal(period)


def _bullish_candle(bar: MarketBar) -> bool:
    body = abs(bar.close - bar.open)
    lower_wick = min(bar.open, bar.close) - bar.low
    return bar.close > bar.open or lower_wick >= body * Decimal("2")


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


def _context_hash(
    daily: tuple[MarketBar, ...],
    weekly: tuple[MarketBar, ...],
    hourly: tuple[MarketBar, ...],
) -> str:
    payload = tuple(
        (
            bar.timeframe.value,
            bar.timestamp.isoformat(),
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
            str(bar.volume),
        )
        for bar in (*daily, *weekly, *hourly)
    )
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"
