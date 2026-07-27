"""Regime-aware Long Term v2 while preserving the v1 implementation."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, MarketBar, NamedValue

from .engine import LongTermEngine
from .models import LongTermContext

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class LongTermEngineV2(LongTermEngine):
    """Add volatility regime and ATR-normalized entry location to Long v1."""

    engine_version = "2.0.0"

    def analyze(
        self,
        context: LongTermContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        if len(context.daily_bars) < 30:
            return result

        atr14 = _atr(context.daily_bars)
        atr_percentile = _atr_percentile(context.daily_bars)
        adx, plus_di, minus_di = _directional_index(context.daily_bars)
        regime = _regime(adx, plus_di, minus_di, atr_percentile)
        metrics = _metric_map(result)
        zone_low = _decimal(metrics.get("buy_zone_low"))
        zone_high = _decimal(metrics.get("buy_zone_high"))
        distance = (
            _distance_to_zone_atr(context.price, zone_low, zone_high, atr14)
            if zone_low is not None and zone_high is not None
            else None
        )

        score = result.score
        score += {
            "clean_uptrend": Decimal("5"),
            "volatile_uptrend": ZERO,
            "quiet_range": Decimal("-5"),
            "choppy": Decimal("-15"),
            "downtrend": Decimal("-20"),
            "transitional": Decimal("-3"),
        }[regime]
        if distance is not None:
            if distance == ZERO:
                score += Decimal("8")
            elif ZERO < distance <= Decimal("1"):
                score += Decimal("4")
            elif distance > Decimal("3"):
                score -= Decimal("10")
        score = _score(score)

        verdict = result.verdict
        if verdict is AnalysisVerdict.FAVORABLE and regime in {"choppy", "downtrend"}:
            verdict = AnalysisVerdict.WATCH
        reasons = (*result.reasons, f"market_regime:{regime}")
        if distance is not None:
            reasons = (*reasons, f"distance_to_buy_zone_atr:{distance}")
        return result.model_copy(
            update={
                "score": score,
                "confidence": (score / HUNDRED).quantize(Decimal("0.0001")),
                "verdict": verdict,
                "reasons": reasons,
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="daily_atr14", value=atr14),
                    NamedValue(name="daily_atr_percentile", value=atr_percentile),
                    NamedValue(name="daily_adx14", value=adx),
                    NamedValue(name="daily_plus_di14", value=plus_di),
                    NamedValue(name="daily_minus_di14", value=minus_di),
                    NamedValue(name="market_regime", value=regime),
                    NamedValue(name="distance_to_buy_zone_atr", value=distance),
                ),
            }
        )


LongTermEngineV1 = LongTermEngine


def _true_ranges(bars: tuple[MarketBar, ...]) -> tuple[Decimal, ...]:
    return tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in pairwise(bars)
    )


def _atr(bars: tuple[MarketBar, ...], period: int = 14) -> Decimal:
    values = _true_ranges(bars)
    return _rounded(sum(values[-period:], ZERO) / Decimal(period))


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
            ZERO if denominator == ZERO else HUNDRED * abs(plus_di - minus_di) / denominator
        )
    adx = sum(dx_values[-period:], ZERO) / Decimal(min(period, len(dx_values)))
    return _rounded(adx), _rounded(plus_di), _rounded(minus_di)


def _regime(adx: Decimal, plus_di: Decimal, minus_di: Decimal, atr_pct: Decimal) -> str:
    if adx >= Decimal("25") and plus_di > minus_di:
        return "volatile_uptrend" if atr_pct >= Decimal("75") else "clean_uptrend"
    if adx >= Decimal("25") and minus_di > plus_di:
        return "downtrend"
    if adx < Decimal("20"):
        return "choppy" if atr_pct >= Decimal("75") else "quiet_range"
    return "transitional"


def _distance_to_zone_atr(
    price: Decimal, low: Decimal, high: Decimal, atr14: Decimal
) -> Decimal:
    if low <= price <= high:
        return ZERO
    boundary = high if price > high else low
    return _rounded((price - boundary) / atr14)


def _metric_map(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
