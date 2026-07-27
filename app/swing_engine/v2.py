"""Regime-aware Swing v2 while preserving the v1 implementation."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from uuid import UUID

from app.contracts import (
    AnalysisResult,
    AnalysisVerdict,
    MarketBar,
    NamedValue,
    PatternDirection,
)

from .engine import SwingEngine
from .models import SwingContext

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class SwingEngineV2(SwingEngine):
    """Evaluate pullbacks relative to regime and an ATR-normalized entry zone."""

    engine_version = "2.0.0"

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        if len(context.daily_bars) < 30:
            return result

        metrics = _metric_map(result)
        atr14 = _required_decimal(metrics, "atr14")
        sma20 = _required_decimal(metrics, "daily_sma20")
        sma50 = _required_decimal(metrics, "daily_sma50")
        support = _required_decimal(metrics, "support")
        resistance = _required_decimal(metrics, "resistance")
        invalidation = _required_decimal(metrics, "invalidation")
        atr_percentile = _atr_percentile(context.daily_bars)
        adx, plus_di, minus_di = _directional_index(context.daily_bars)
        slope = _percent_vs(sma20, _sma(tuple(bar.close for bar in context.daily_bars[:-5]), 20))
        regime = _regime(adx, plus_di, minus_di, atr_percentile, slope)
        structure_broken = (
            context.price < sma50 - atr14 * Decimal("1.5")
            and slope < ZERO
            and adx >= Decimal("20")
            and minus_di > plus_di
        )

        anchors = tuple(
            value
            for name in ("pivot_low_avwap", "breakout_avwap")
            if (value := _decimal(metrics.get(name))) is not None
        )
        anchor = min(
            (support, sma20, sma50, *anchors),
            key=lambda value: abs(context.price - value),
        )
        entry_low = max(Decimal("0.0001"), anchor - atr14 * Decimal("0.5"))
        entry_high = anchor + atr14 * Decimal("0.5")
        distance = _distance_to_zone_atr(context.price, entry_low, entry_high, atr14)
        risk = context.price - invalidation
        reward_risk = (
            ZERO
            if risk <= ZERO or resistance <= context.price
            else (resistance - context.price) / risk
        )

        classification = str(metrics.get("classification", "setup"))
        risk_ok = (
            _required_decimal(metrics, "risk_percent") <= Decimal("8")
            and _required_decimal(metrics, "risk_atr") <= Decimal("3")
        )
        if structure_broken:
            classification = "avoid"
        elif classification == "avoid":
            near_zone_in_uptrend = (
                regime in {"clean_uptrend", "volatile_uptrend"}
                and abs(distance) <= Decimal("1")
            )
            classification = "pullback" if near_zone_in_uptrend else "setup"
        elif (
            classification == "setup"
            and regime in {"clean_uptrend", "volatile_uptrend"}
            and abs(distance) <= Decimal("1")
            and risk_ok
        ):
            classification = "pullback"
        elif classification == "pullback" and regime in {"choppy", "downtrend"}:
            classification = "setup" if not structure_broken else "avoid"

        score = result.score
        score += {
            "clean_uptrend": Decimal("8"),
            "volatile_uptrend": Decimal("2"),
            "quiet_range": Decimal("-4"),
            "choppy": Decimal("-18"),
            "downtrend": Decimal("-25"),
            "transitional": Decimal("-5"),
        }[regime]
        if distance == ZERO:
            score += Decimal("8")
        elif abs(distance) <= Decimal("1"):
            score += Decimal("4")
        elif distance > Decimal("2"):
            score -= Decimal("10")
        if reward_risk >= Decimal("2"):
            score += Decimal("5")
        elif reward_risk > ZERO and reward_risk < Decimal("1.5"):
            score -= Decimal("10")
        score = _score(score)

        verdict = _verdict(classification, risk_ok, score, regime)
        direction = (
            PatternDirection.BEARISH
            if classification == "avoid"
            else PatternDirection.BULLISH
        )
        reasons = (
            *result.reasons,
            f"market_regime:{regime}",
            f"entry_zone_distance_atr:{distance}",
        )
        if structure_broken:
            reasons = (*reasons, "confirmed_daily_structure_break")
        return result.model_copy(
            update={
                "verdict": verdict,
                "direction": direction,
                "score": score,
                "confidence": (score / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": reasons,
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="classification", value=classification),
                    NamedValue(name="daily_atr_percentile", value=atr_percentile),
                    NamedValue(name="daily_adx14", value=adx),
                    NamedValue(name="daily_plus_di14", value=plus_di),
                    NamedValue(name="daily_minus_di14", value=minus_di),
                    NamedValue(name="market_regime", value=regime),
                    NamedValue(name="structure_broken_confirmed", value=structure_broken),
                    NamedValue(name="entry_zone_low", value=_rounded(entry_low)),
                    NamedValue(name="entry_zone_high", value=_rounded(entry_high)),
                    NamedValue(name="price_vs_entry_zone_atr", value=distance),
                    NamedValue(name="reward_risk_to_resistance", value=_rounded(reward_risk)),
                ),
            }
        )


SwingEngineV1 = SwingEngine


def _verdict(
    classification: str, risk_ok: bool, score: Decimal, regime: str
) -> AnalysisVerdict:
    if classification == "avoid":
        return AnalysisVerdict.AVOID
    if classification == "extended":
        return AnalysisVerdict.CAUTION
    if classification in {"pullback", "breakout"} and risk_ok:
        if regime in {"choppy", "downtrend"}:
            return AnalysisVerdict.CAUTION
        return AnalysisVerdict.FAVORABLE if score >= Decimal("65") else AnalysisVerdict.WATCH
    return AnalysisVerdict.WATCH


def _true_ranges(bars: tuple[MarketBar, ...]) -> tuple[Decimal, ...]:
    return tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars)
    )


def _atr_percentile(
    bars: tuple[MarketBar, ...], period: int = 14, lookback: int = 100
) -> Decimal:
    ranges = _true_ranges(bars)
    samples = tuple(
        sum(ranges[end - period : end], ZERO) / Decimal(period)
        for end in range(period, len(ranges) + 1)
    )[-lookback:]
    current = samples[-1]
    less = sum(value < current for value in samples)
    equal = sum(value == current for value in samples)
    rank = (Decimal(less) + Decimal(equal) / Decimal("2")) / Decimal(len(samples))
    return _rounded(rank * HUNDRED)


def _directional_index(
    bars: tuple[MarketBar, ...], period: int = 14
) -> tuple[Decimal, Decimal, Decimal]:
    ranges = _true_ranges(bars)
    plus_dm: list[Decimal] = []
    minus_dm: list[Decimal] = []
    for previous, current in pairwise(bars):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > ZERO else ZERO)
        minus_dm.append(down if down > up and down > ZERO else ZERO)
    dx_values: list[Decimal] = []
    plus_di = ZERO
    minus_di = ZERO
    for end in range(period, len(ranges) + 1):
        tr_sum = sum(ranges[end - period : end], ZERO)
        if tr_sum <= ZERO:
            continue
        plus_di = HUNDRED * sum(plus_dm[end - period : end], ZERO) / tr_sum
        minus_di = HUNDRED * sum(minus_dm[end - period : end], ZERO) / tr_sum
        denominator = plus_di + minus_di
        dx_values.append(
            ZERO
            if denominator == ZERO
            else HUNDRED * abs(plus_di - minus_di) / denominator
        )
    adx = sum(dx_values[-period:], ZERO) / Decimal(min(period, len(dx_values)))
    return _rounded(adx), _rounded(plus_di), _rounded(minus_di)


def _regime(
    adx: Decimal,
    plus_di: Decimal,
    minus_di: Decimal,
    atr_pct: Decimal,
    slope: Decimal,
) -> str:
    if adx >= Decimal("25") and plus_di > minus_di and slope >= ZERO:
        return "volatile_uptrend" if atr_pct >= Decimal("75") else "clean_uptrend"
    if adx >= Decimal("25") and minus_di > plus_di and slope < ZERO:
        return "downtrend"
    if adx < Decimal("20"):
        return "choppy" if atr_pct >= Decimal("75") else "quiet_range"
    return "transitional"


def _sma(values: tuple[Decimal, ...], period: int) -> Decimal:
    return sum(values[-period:], ZERO) / Decimal(period)


def _percent_vs(value: Decimal, reference: Decimal) -> Decimal:
    return _rounded((value - reference) / reference * HUNDRED)


def _distance_to_zone_atr(
    price: Decimal, low: Decimal, high: Decimal, atr14: Decimal
) -> Decimal:
    if low <= price <= high:
        return ZERO
    return _rounded((price - (high if price > high else low)) / atr14)


def _metric_map(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _required_decimal(metrics: dict[str, object], name: str) -> Decimal:
    value = _decimal(metrics.get(name))
    if value is None:
        raise ValueError(f"missing decimal metric: {name}")
    return value


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
