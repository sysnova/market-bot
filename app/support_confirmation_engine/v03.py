# pyright: reportPrivateUsage=false
"""Support Confirmation 0.3: actionable zones and intraday structural evidence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.contracts import (
    MarketBar,
    NamedValue,
    SupportAssessment,
    SupportConfirmationType,
    SupportState,
    SupportZonePosition,
)

from .engine import (
    HUNDRED,
    ZERO,
    _atr,
    _context_hash,
    _features,
    _Level,
    _reaction_score,
    _recent_impulse,
    _reversal_score,
    _rounded,
    _state,
    _structural_supports,
    _support_levels,
)
from .models import SupportContext, SupportZoneHint

MAX_CLUSTER_DISTANCE_ATR = Decimal("2.0")
MAX_SINGLE_DISTANCE_ATR = Decimal("1.5")
REVERSAL_PENDING_CAP = Decimal("59")


@dataclass(frozen=True, slots=True)
class _ZoneTelemetry:
    position: SupportZonePosition
    distance_percent: Decimal
    distance_atr: Decimal
    touch_count: int
    touch_age_sessions: int | None


@dataclass(frozen=True, slots=True)
class _FourHourBar:
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class _FourHourEvidence:
    reclaim: bool = False
    higher_high: bool = False
    higher_low: bool = False


class SupportConfirmationEngineV03:
    """Separate support discovery, reaction, and structural confirmation."""

    engine_id = "support-confirmation"
    engine_version = "0.3.0"

    def evaluate(self, context: SupportContext) -> SupportAssessment:
        daily = tuple(bar for bar in context.daily_bars if bar.is_final)
        weekly = tuple(bar for bar in context.weekly_bars if bar.is_final)
        hourly = tuple(bar for bar in context.hourly_bars if bar.is_final)
        if len(daily) < 15:
            raise ValueError("Support Confirmation requires 15 completed daily bars")

        atr14 = max(_atr(daily), Decimal("0.0001"))
        price = daily[-1].close
        levels = _support_levels(daily, weekly, atr14)
        structural_supports = _structural_supports(levels, price, atr14)
        impulse = _recent_impulse(daily, atr14)
        zone = context.zone_hint or _best_cluster_zone(daily, levels, atr14)
        is_single_source = False
        if zone is None:
            zone = _best_single_zone(daily, levels, atr14)
            is_single_source = zone is not None

        symbol = context.symbol.strip().upper()
        occurred_at = daily[-1].timestamp
        context_hash = _context_hash(daily, weekly, hourly)
        impulse_origin = impulse.origin if impulse is not None else None
        impulse_origin_at = impulse.origin_at if impulse is not None else None
        impulse_peak = impulse.peak if impulse is not None else None
        impulse_advance_percent = impulse.advance_percent if impulse is not None else None
        if zone is None:
            return SupportAssessment(
                symbol=symbol,
                occurred_at=occurred_at,
                engine_version=self.engine_version,
                state=SupportState.NO_NEARBY_SUPPORT,
                current_price=price,
                support_score=ZERO,
                reaction_score=ZERO,
                reversal_score=ZERO,
                confidence=ZERO,
                structural_supports=structural_supports,
                impulse_origin=impulse_origin,
                impulse_origin_at=impulse_origin_at,
                impulse_peak=impulse_peak,
                impulse_advance_percent=impulse_advance_percent,
                reasons=("no_actionable_support_within_distance",),
                context_hash=context_hash,
            )

        telemetry = _zone_telemetry(daily, zone, atr14)
        if is_single_source:
            actionability = _actionability_score(
                zone.score, ZERO, ZERO, telemetry.distance_atr, telemetry.touch_age_sessions
            )
            return SupportAssessment(
                symbol=symbol,
                occurred_at=occurred_at,
                engine_version=self.engine_version,
                state=SupportState.SINGLE_SUPPORT_NEARBY,
                current_price=price,
                zone_low=_rounded(zone.low),
                zone_center=_rounded(zone.center),
                zone_high=_rounded(zone.high),
                invalidation=_rounded(zone.invalidation),
                support_score=_rounded(zone.score),
                reaction_score=ZERO,
                reversal_score=ZERO,
                confidence=_rounded(min(Decimal("0.49"), actionability / HUNDRED)),
                zone_position=telemetry.position,
                zone_distance_percent=_rounded(telemetry.distance_percent),
                zone_distance_atr=_rounded(telemetry.distance_atr),
                touch_count=telemetry.touch_count,
                touch_age_sessions=telemetry.touch_age_sessions,
                actionability_score=_rounded(actionability),
                support_sources=zone.sources,
                structural_supports=structural_supports,
                impulse_origin=impulse_origin,
                impulse_origin_at=impulse_origin_at,
                impulse_peak=impulse_peak,
                impulse_advance_percent=impulse_advance_percent,
                reasons=(
                    "single_support_nearby_without_independent_confluence",
                    "informational_only_not_swing_confirmation",
                ),
                metrics=(NamedValue(name="atr14_daily", value=_rounded(atr14)),),
                context_hash=context_hash,
            )

        daily_features = _features(daily, zone, atr14)
        four_hour = _four_hour_evidence(hourly, zone, atr14)
        combined = replace(
            daily_features,
            reclaimed=daily_features.reclaimed or four_hour.reclaim,
            higher_high=daily_features.higher_high or four_hour.higher_high,
            higher_low=daily_features.higher_low or four_hour.higher_low,
        )
        reaction_score = _reaction_score(zone.score, combined)
        reversal_score = _reversal_score(daily, reaction_score, combined)
        paired_structure = (daily_features.higher_high and daily_features.higher_low) or (
            four_hour.higher_high and four_hour.higher_low
        )
        if not paired_structure:
            reversal_score = min(reversal_score, REVERSAL_PENDING_CAP)

        state, confirmation_type, reasons = _state(
            context.previous_assessment,
            price,
            zone,
            reaction_score,
            reversal_score,
            combined,
        )
        if state in {SupportState.BASE_BUILDING, SupportState.LIQUIDITY_SWEEP}:
            confirmation_type = SupportConfirmationType.NONE
        b_wave_risk = reaction_score >= Decimal("60") and reversal_score < Decimal("60")
        actionability = _actionability_score(
            zone.score,
            reaction_score,
            reversal_score,
            telemetry.distance_atr,
            telemetry.touch_age_sessions,
        )
        confidence = _stage_confidence(state, zone.score, reaction_score, reversal_score)
        return SupportAssessment(
            symbol=symbol,
            occurred_at=occurred_at,
            engine_version=self.engine_version,
            state=state,
            confirmation_type=confirmation_type,
            current_price=price,
            zone_low=_rounded(zone.low),
            zone_center=_rounded(zone.center),
            zone_high=_rounded(zone.high),
            invalidation=_rounded(zone.invalidation),
            support_score=_rounded(zone.score),
            reaction_score=_rounded(reaction_score),
            reversal_score=_rounded(reversal_score),
            confidence=_rounded(confidence),
            liquidity_sweep=combined.liquidity_sweep,
            higher_high=combined.higher_high,
            higher_low=combined.higher_low,
            b_wave_risk=b_wave_risk,
            zone_position=telemetry.position,
            zone_distance_percent=_rounded(telemetry.distance_percent),
            zone_distance_atr=_rounded(telemetry.distance_atr),
            touch_count=telemetry.touch_count,
            touch_age_sessions=telemetry.touch_age_sessions,
            four_hour_reclaim=four_hour.reclaim,
            four_hour_higher_high=four_hour.higher_high,
            four_hour_higher_low=four_hour.higher_low,
            actionability_score=_rounded(actionability),
            support_sources=zone.sources,
            structural_supports=structural_supports,
            impulse_origin=impulse_origin,
            impulse_origin_at=impulse_origin_at,
            impulse_peak=impulse_peak,
            impulse_advance_percent=impulse_advance_percent,
            reasons=reasons,
            metrics=(
                NamedValue(name="atr14_daily", value=_rounded(atr14)),
                NamedValue(name="reaction_rvol", value=_rounded(combined.max_recent_rvol)),
                NamedValue(name="pre_touch_high", value=_rounded(combined.pre_touch_high)),
                NamedValue(name="base_building", value=combined.base_building),
                NamedValue(name="base_breakout", value=combined.base_breakout),
                NamedValue(name="paired_structure", value=paired_structure),
            ),
            context_hash=context_hash,
        )


def _best_cluster_zone(
    daily: tuple[MarketBar, ...], levels: tuple[_Level, ...], atr14: Decimal
) -> SupportZoneHint | None:
    price = daily[-1].close
    candidates: list[tuple[Decimal, SupportZoneHint]] = []
    for seed in levels:
        cluster = tuple(
            level for level in levels if abs(level.value - seed.value) <= atr14 * Decimal("0.35")
        )
        sources = tuple(dict.fromkeys(level.source for level in cluster))
        families = {_source_family(level.source) for level in cluster}
        families.discard("round")
        if len(sources) < 2 or len(families) < 2:
            continue
        center = sum((level.value for level in cluster), ZERO) / Decimal(len(cluster))
        low = min(level.value for level in cluster) - atr14 * Decimal("0.10")
        high = max(level.value for level in cluster) + atr14 * Decimal("0.10")
        invalidation = low - atr14 * Decimal("0.75")
        if price <= invalidation or price > high + atr14 * MAX_CLUSTER_DISTANCE_ATR:
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
        distance_atr = _distance_to_zone(price, low, high) / atr14
        rank = score - distance_atr * Decimal("15")
        candidates.append(
            (
                rank,
                SupportZoneHint(
                    low=_rounded(low),
                    center=_rounded(center),
                    high=_rounded(high),
                    invalidation=_rounded(invalidation),
                    score=_rounded(score),
                    sources=sources,
                ),
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1].score))[1]


def _best_single_zone(
    daily: tuple[MarketBar, ...], levels: tuple[_Level, ...], atr14: Decimal
) -> SupportZoneHint | None:
    price = daily[-1].close
    candidates: list[tuple[Decimal, _Level, int]] = []
    for level in levels:
        if level.source == "round_number" or level.value > price + atr14 * Decimal("0.25"):
            continue
        distance_atr = abs(price - level.value) / atr14
        if distance_atr > MAX_SINGLE_DISTANCE_ATR:
            continue
        defenses = sum(
            abs(bar.low - level.value) <= atr14 * Decimal("0.25") and bar.close > level.value
            for bar in daily[-120:]
        )
        rank = level.points + Decimal(min(3, defenses) * 4) - distance_atr * Decimal("10")
        candidates.append((rank, level, defenses))
    if not candidates:
        return None
    _, selected, defenses = max(candidates, key=lambda item: (item[0], item[1].value))
    low = selected.value - atr14 * Decimal("0.10")
    high = selected.value + atr14 * Decimal("0.10")
    score = min(Decimal("49"), selected.points + Decimal(min(3, defenses) * 4))
    return SupportZoneHint(
        low=_rounded(low),
        center=_rounded(selected.value),
        high=_rounded(high),
        invalidation=_rounded(low - atr14 * Decimal("0.75")),
        score=_rounded(score),
        sources=(selected.source,),
    )


def _source_family(source: str) -> str:
    if source.startswith("pivot_daily_"):
        return "daily_pivot"
    if source.startswith("pivot_weekly_"):
        return "weekly_pivot"
    if source.startswith("daily_sma"):
        return "daily_average"
    if source.startswith("weekly_sma"):
        return "weekly_average"
    if source.startswith("fib_"):
        return "fibonacci"
    if source == "round_number":
        return "round"
    return source


def _zone_telemetry(
    daily: tuple[MarketBar, ...], zone: SupportZoneHint, atr14: Decimal
) -> _ZoneTelemetry:
    price = daily[-1].close
    if price < zone.low:
        position = SupportZonePosition.BELOW_ZONE
    elif price <= zone.high:
        position = SupportZonePosition.IN_ZONE
    else:
        position = SupportZonePosition.ABOVE_ZONE
    distance = _distance_to_zone(price, zone.low, zone.high)
    recent = daily[-12:]
    padding = atr14 * Decimal("0.15")
    touches = tuple(
        index
        for index, bar in enumerate(recent)
        if bar.low <= zone.high + padding and bar.high >= zone.low - padding
    )
    age = len(recent) - 1 - touches[-1] if touches else None
    return _ZoneTelemetry(
        position=position,
        distance_percent=distance / price * HUNDRED,
        distance_atr=distance / atr14,
        touch_count=len(touches),
        touch_age_sessions=age,
    )


def _distance_to_zone(price: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if price < low:
        return low - price
    if price > high:
        return price - high
    return ZERO


def _four_hour_evidence(
    hourly: tuple[MarketBar, ...], zone: SupportZoneHint, atr14: Decimal
) -> _FourHourEvidence:
    if len(hourly) < 8:
        return _FourHourEvidence()
    bars = _four_hour_bars(hourly[-40:])[-8:]
    if len(bars) < 2:
        return _FourHourEvidence()
    padding = atr14 * Decimal("0.10")
    touches = tuple(
        index
        for index, bar in enumerate(bars)
        if bar.low <= zone.high + padding and bar.high >= zone.low - padding
    )
    if not touches:
        return _FourHourEvidence()
    after_touch = bars[touches[-1] :]
    reclaim = bars[-1].close >= zone.high
    if len(after_touch) < 2:
        return _FourHourEvidence(reclaim=reclaim)
    previous, current = after_touch[-2:]
    return _FourHourEvidence(
        reclaim=reclaim,
        higher_high=current.high > previous.high and current.close > previous.close,
        higher_low=current.low > previous.low and current.low > zone.low,
    )


def _four_hour_bars(hourly: tuple[MarketBar, ...]) -> tuple[_FourHourBar, ...]:
    # Four completed 1H observations form one evidence block. Align from the
    # newest data so an incomplete block at the beginning is discarded rather
    # than allowing a partial current block to masquerade as 4H confirmation.
    offset = len(hourly) % 4
    selected = hourly[offset:]
    buckets = tuple(selected[index : index + 4] for index in range(0, len(selected), 4))
    return tuple(
        _FourHourBar(
            high=max(bar.high for bar in bucket),
            low=min(bar.low for bar in bucket),
            close=bucket[-1].close,
        )
        for bucket in buckets
    )


def _stage_confidence(
    state: SupportState,
    support_score: Decimal,
    reaction_score: Decimal,
    reversal_score: Decimal,
) -> Decimal:
    if state in {SupportState.STRUCTURE_CONFIRMED, SupportState.RETEST_CONFIRMED}:
        score = (
            support_score * Decimal("0.20")
            + reaction_score * Decimal("0.30")
            + reversal_score * Decimal("0.50")
        )
    elif state in {
        SupportState.REACTION_CONFIRMED,
        SupportState.RECLAIMED,
        SupportState.LIQUIDITY_SWEEP,
    }:
        score = (
            support_score * Decimal("0.30")
            + reaction_score * Decimal("0.45")
            + reversal_score * Decimal("0.25")
        )
    else:
        score = (
            support_score * Decimal("0.50")
            + reaction_score * Decimal("0.30")
            + reversal_score * Decimal("0.20")
        )
    return min(Decimal("1"), score / HUNDRED)


def _actionability_score(
    support_score: Decimal,
    reaction_score: Decimal,
    reversal_score: Decimal,
    distance_atr: Decimal,
    touch_age: int | None,
) -> Decimal:
    score = (
        support_score * Decimal("0.35")
        + reaction_score * Decimal("0.35")
        + reversal_score * Decimal("0.30")
    )
    score -= min(Decimal("30"), distance_atr * Decimal("15"))
    if touch_age is None:
        score -= Decimal("8")
    else:
        score -= Decimal(min(20, touch_age * 2))
    return max(ZERO, min(HUNDRED, score))
