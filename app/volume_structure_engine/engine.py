"""Deterministic weekly price/OBV divergence policy."""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    MarketBar,
    NamedValue,
    PatternDirection,
)

from .models import VolumeStructureContext

ZERO = Decimal()
HUNDRED = Decimal("100")
FOUR_PLACES = Decimal("0.0001")
MINIMUM_BARS = 12
PIVOT_RADIUS = 2
MINIMUM_PIVOT_SEPARATION = 3
MAXIMUM_PIVOT_SEPARATION = 26
MINIMUM_PRICE_DROP_PERCENT = Decimal("1")
MINIMUM_PRICE_DROP_ATR = Decimal("0.25")
MINIMUM_OBV_IMPROVEMENT = Decimal("0.5")


class VolumeStructureEngine:
    """Detect regular bullish divergence without claiming buyer identity."""

    engine_id = "volume-structure"
    engine_version = "1.0.0"

    def evaluate(self, context: VolumeStructureContext) -> AnalysisResult:
        bars = tuple(bar for bar in context.weekly_bars if bar.is_final)
        if len(bars) < MINIMUM_BARS:
            return _result(
                context,
                bars,
                verdict=AnalysisVerdict.INSUFFICIENT_DATA,
                direction=PatternDirection.NEUTRAL,
                score=ZERO,
                confidence=ZERO,
                reasons=(f"weekly_history_insufficient:{len(bars)}<{MINIMUM_BARS}",),
                metrics=(
                    NamedValue(name="divergence_state", value="NO_DIVERGENCE"),
                    NamedValue(name="evidence_boost", value=ZERO),
                ),
            )

        obv = _obv(bars)
        atr = _atr(bars)
        average_volume = _average_volume(bars)
        pivots = _confirmed_low_pivots(bars)
        pair = _latest_divergence_pair(
            bars,
            obv,
            pivots,
            atr=atr,
            average_volume=average_volume,
        )
        if pair is None:
            developing = _developing_divergence(
                bars,
                obv,
                pivots,
                atr=atr,
                average_volume=average_volume,
            )
            if developing is not None:
                return self._divergence_result(
                    context,
                    bars,
                    obv,
                    developing[0],
                    developing[1],
                    atr=atr,
                    average_volume=average_volume,
                    state="DEVELOPING",
                    boost=Decimal("2"),
                )
            return _result(
                context,
                bars,
                verdict=AnalysisVerdict.WATCH,
                direction=PatternDirection.NEUTRAL,
                score=ZERO,
                confidence=ZERO,
                reasons=("weekly_obv_bullish_divergence_absent",),
                metrics=(
                    NamedValue(name="divergence_state", value="NO_DIVERGENCE"),
                    NamedValue(name="weekly_close", value=bars[-1].close),
                    NamedValue(name="weekly_atr", value=_rounded(atr)),
                    NamedValue(name="evidence_boost", value=ZERO),
                ),
            )

        first, second = pair
        ema10 = _ema(tuple(bar.close for bar in bars), 10)
        reclaim_trigger = max(bars[second].high, ema10)
        reclaimed = bars[-1].close >= reclaim_trigger
        return self._divergence_result(
            context,
            bars,
            obv,
            first,
            second,
            atr=atr,
            average_volume=average_volume,
            state="RECLAIM_CONFIRMED" if reclaimed else "DIVERGENCE_CONFIRMED",
            boost=Decimal("10") if reclaimed else Decimal("6"),
            reclaim_trigger=reclaim_trigger,
        )

    def _divergence_result(
        self,
        context: VolumeStructureContext,
        bars: tuple[MarketBar, ...],
        obv: tuple[Decimal, ...],
        first: int,
        second: int,
        *,
        atr: Decimal,
        average_volume: Decimal,
        state: str,
        boost: Decimal,
        reclaim_trigger: Decimal | None = None,
    ) -> AnalysisResult:
        price_change = (bars[second].low - bars[first].low) / bars[first].low * HUNDRED
        obv_improvement = (
            ZERO
            if average_volume <= ZERO
            else (obv[second] - obv[first]) / average_volume
        )
        score = min(
            HUNDRED,
            Decimal("60")
            + min(Decimal("15"), abs(price_change))
            + min(Decimal("15"), obv_improvement * Decimal("10"))
            + (Decimal("10") if state == "RECLAIM_CONFIRMED" else ZERO),
        )
        invalidation = max(
            Decimal("0.0001"), bars[second].low - atr * Decimal("0.5")
        )
        metrics = [
            NamedValue(name="divergence_state", value=state),
            NamedValue(name="weekly_close", value=bars[-1].close),
            NamedValue(name="price_pivot_1", value=bars[first].low),
            NamedValue(name="price_pivot_2", value=bars[second].low),
            NamedValue(name="price_pivot_1_at", value=bars[first].timestamp),
            NamedValue(name="price_pivot_2_at", value=bars[second].timestamp),
            NamedValue(name="price_change_percent", value=_rounded(price_change)),
            NamedValue(name="obv_pivot_1", value=obv[first]),
            NamedValue(name="obv_pivot_2", value=obv[second]),
            NamedValue(name="obv_improvement_normalized", value=_rounded(obv_improvement)),
            NamedValue(name="pivot_separation_weeks", value=second - first),
            NamedValue(name="weekly_atr", value=_rounded(atr)),
            NamedValue(name="invalidation", value=_rounded(invalidation)),
            NamedValue(name="evidence_boost", value=boost),
        ]
        if reclaim_trigger is not None:
            metrics.append(
                NamedValue(name="reclaim_trigger", value=_rounded(reclaim_trigger))
            )
        reasons = [
            "weekly_obv_bullish_divergence",
            "price_lower_low",
            "obv_higher_low",
            "possible_accumulation_or_selling_pressure_absorption",
        ]
        if state == "RECLAIM_CONFIRMED":
            reasons.append("weekly_price_reclaim_confirmed")
        elif state == "DEVELOPING":
            reasons.append("current_week_pivot_unconfirmed")
        return _result(
            context,
            bars,
            verdict=(
                AnalysisVerdict.FAVORABLE
                if state != "DEVELOPING"
                else AnalysisVerdict.WATCH
            ),
            direction=PatternDirection.BULLISH,
            score=_rounded(score),
            confidence=_rounded(min(Decimal("1"), score / HUNDRED)),
            reasons=tuple(reasons),
            metrics=tuple(metrics),
        )


def _latest_divergence_pair(
    bars: tuple[MarketBar, ...],
    obv: tuple[Decimal, ...],
    pivots: tuple[int, ...],
    *,
    atr: Decimal,
    average_volume: Decimal,
) -> tuple[int, int] | None:
    for second_position in range(len(pivots) - 1, 0, -1):
        second = pivots[second_position]
        for first in reversed(pivots[:second_position]):
            separation = second - first
            if separation > MAXIMUM_PIVOT_SEPARATION:
                break
            if separation < MINIMUM_PIVOT_SEPARATION:
                continue
            if _is_divergence(
                bars,
                obv,
                first,
                second,
                atr=atr,
                average_volume=average_volume,
            ):
                return first, second
    return None


def _developing_divergence(
    bars: tuple[MarketBar, ...],
    obv: tuple[Decimal, ...],
    pivots: tuple[int, ...],
    *,
    atr: Decimal,
    average_volume: Decimal,
) -> tuple[int, int] | None:
    if not pivots or len(bars) - 1 - pivots[-1] > MAXIMUM_PIVOT_SEPARATION:
        return None
    first = pivots[-1]
    second = len(bars) - 1
    if second - first < MINIMUM_PIVOT_SEPARATION:
        return None
    return (
        (first, second)
        if _is_divergence(
            bars,
            obv,
            first,
            second,
            atr=atr,
            average_volume=average_volume,
        )
        else None
    )


def _is_divergence(
    bars: tuple[MarketBar, ...],
    obv: tuple[Decimal, ...],
    first: int,
    second: int,
    *,
    atr: Decimal,
    average_volume: Decimal,
) -> bool:
    price_drop = bars[first].low - bars[second].low
    price_drop_percent = price_drop / bars[first].low * HUNDRED
    obv_improvement = obv[second] - obv[first]
    normalized_obv = ZERO if average_volume <= ZERO else obv_improvement / average_volume
    meaningful_price_low = price_drop > ZERO and (
        price_drop_percent >= MINIMUM_PRICE_DROP_PERCENT
        or price_drop >= atr * MINIMUM_PRICE_DROP_ATR
    )
    return meaningful_price_low and normalized_obv >= MINIMUM_OBV_IMPROVEMENT


def _confirmed_low_pivots(
    bars: tuple[MarketBar, ...], radius: int = PIVOT_RADIUS
) -> tuple[int, ...]:
    output: list[int] = []
    for index in range(radius, len(bars) - radius):
        neighbors = (*bars[index - radius : index], *bars[index + 1 : index + radius + 1])
        if all(bars[index].low <= bar.low for bar in neighbors) and any(
            bars[index].low < bar.low for bar in neighbors
        ):
            output.append(index)
    return tuple(output)


def _obv(bars: tuple[MarketBar, ...]) -> tuple[Decimal, ...]:
    values = [ZERO]
    for previous, current in pairwise(bars):
        if current.close > previous.close:
            values.append(values[-1] + current.volume)
        elif current.close < previous.close:
            values.append(values[-1] - current.volume)
        else:
            values.append(values[-1])
    return tuple(values)


def _atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    selected = bars[-min(len(bars), period + 1) :]
    ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(selected)
    )
    return sum(ranges, ZERO) / Decimal(len(ranges))


def _average_volume(bars: tuple[MarketBar, ...], period: int = 20) -> Decimal:
    selected = bars[-period:]
    return sum((bar.volume for bar in selected), ZERO) / Decimal(len(selected))


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    selected = values[-period:]
    multiplier = Decimal("2") / Decimal(period + 1)
    value = selected[0]
    for item in selected[1:]:
        value = (item - value) * multiplier + value
    return value


def _result(
    context: VolumeStructureContext,
    bars: tuple[MarketBar, ...],
    *,
    verdict: AnalysisVerdict,
    direction: PatternDirection,
    score: Decimal,
    confidence: Decimal,
    reasons: tuple[str, ...],
    metrics: tuple[NamedValue, ...],
) -> AnalysisResult:
    as_of = bars[-1].timestamp if bars else context.weekly_bars[-1].timestamp
    return AnalysisResult(
        engine_id=VolumeStructureEngine.engine_id,
        engine_version=VolumeStructureEngine.engine_version,
        symbol=context.symbol.strip().upper(),
        horizon=AnalysisHorizon.VOLUME_STRUCTURE,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=score,
        confidence=confidence,
        reasons=reasons,
        metrics=metrics,
        context_hash=_context_hash(bars),
    )


def _context_hash(bars: tuple[MarketBar, ...]) -> str:
    payload = tuple(
        (
            bar.timestamp.isoformat(),
            str(bar.open),
            str(bar.high),
            str(bar.low),
            str(bar.close),
            str(bar.volume),
        )
        for bar in bars
    )
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
