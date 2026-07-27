"""Intraday v2 confirmation quality layered over the stable v1 rules."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue

from .engine import IntradayEngine
from .models import IntradayContext

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_BULLISH_SETUPS = {"bullish_breakout", "bullish_vwap_reclaim"}
_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}


class IntradayEngineV2(IntradayEngine):
    """Require candle, volume and five-minute evidence around v1 triggers."""

    engine_version = "2.0.0"

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        if len(context.minute_bars) < 30:
            return result

        metrics = _metric_map(result)
        latest = context.minute_bars[-1]
        candle_range = latest.high - latest.low
        close_location = (
            Decimal("0.5")
            if candle_range == ZERO
            else (latest.close - latest.low) / candle_range
        )
        previous_volumes = tuple(bar.volume for bar in context.minute_bars[-6:-1])
        volume_average = sum(previous_volumes, ZERO) / Decimal(len(previous_volumes))
        volume_acceleration = ZERO if volume_average == ZERO else latest.volume / volume_average
        higher_low = _five_minute_higher_low(context)
        setup = str(metrics.get("setup", "no_trigger"))
        price_vs_vwap = _decimal(metrics.get("price_vs_vwap_percent"))
        if price_vs_vwap is None:
            session_vwap = _required_decimal(metrics, "session_vwap")
            price_vs_vwap = (latest.close - session_vwap) / session_vwap * HUNDRED
        ema9 = _required_decimal(metrics, "ema9")
        ema20 = _required_decimal(metrics, "ema20")
        momentum = _required_decimal(metrics, "momentum_5_percent")
        five_bias = str(metrics.get("five_minute_bias", "unavailable"))
        regime = _intraday_regime(price_vs_vwap, ema9, ema20, momentum)

        bullish = setup in _BULLISH_SETUPS
        bearish = setup in _BEARISH_SETUPS
        aligned_five = five_bias in {
            "bullish" if bullish else "bearish" if bearish else "unavailable",
            "unavailable",
        }
        strong = (
            (bullish or bearish)
            and aligned_five
            and (higher_low if bullish else True)
            and (
                close_location >= Decimal("0.65")
                if bullish
                else close_location <= Decimal("0.35")
            )
            and volume_acceleration >= Decimal("1.2")
        )
        standard = (
            (bullish or bearish)
            and aligned_five
            and (
                close_location >= Decimal("0.55")
                if bullish
                else close_location <= Decimal("0.45")
            )
        )
        quality = "strong" if strong else "standard" if standard else "weak"

        verdict = result.verdict
        score = result.score
        if (bullish or bearish) and quality == "weak":
            verdict = AnalysisVerdict.WATCH
            score = min(score, Decimal("64"))
        elif strong:
            score = min(HUNDRED, score + Decimal("5"))
        reasons = (
            *result.reasons,
            f"intraday_regime:{regime}",
            f"confirmation_quality:{quality}",
        )
        return result.model_copy(
            update={
                "verdict": verdict,
                "score": _score(score),
                "confidence": (_score(score) / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": reasons,
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="close_location", value=_rounded(close_location)),
                    NamedValue(name="volume_acceleration", value=_rounded(volume_acceleration)),
                    NamedValue(name="five_minute_higher_low", value=higher_low),
                    NamedValue(name="intraday_regime", value=regime),
                    NamedValue(name="confirmation_quality", value=quality),
                ),
            }
        )


IntradayEngineV1 = IntradayEngine


def _five_minute_higher_low(context: IntradayContext) -> bool:
    bars = context.five_minute_bars
    if len(bars) < 6:
        return False
    previous_floor = min(bar.low for bar in bars[-6:-3])
    current_floor = min(bar.low for bar in bars[-3:])
    return current_floor > previous_floor and bars[-1].close > bars[-2].close


def _intraday_regime(
    price_vs_vwap: Decimal, ema9: Decimal, ema20: Decimal, momentum: Decimal
) -> str:
    if price_vs_vwap > ZERO and ema9 > ema20 and momentum > ZERO:
        return "bullish_trend"
    if price_vs_vwap < ZERO and ema9 < ema20 and momentum < ZERO:
        return "bearish_trend"
    return "range_or_transition"


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
