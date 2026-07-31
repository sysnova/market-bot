"""Noise-aware Intraday v3 confirmation rules preserving v2 for rollback."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue

from .models import IntradayContext
from .v2 import IntradayEngineV2

ZERO = Decimal("0")
HUNDRED = Decimal("100")
MIN_MOMENTUM_PERCENT = Decimal("0.15")
MIN_RISK_PERCENT = Decimal("0.25")
MIN_ATR_RISK_MULTIPLE = Decimal("0.50")
REWARD_RISK_RATIO = Decimal("1.50")
_BULLISH_SETUPS = {"bullish_breakout", "bullish_vwap_reclaim"}


class IntradayEngineV3(IntradayEngineV2):
    """Delay marginal breakouts and keep tactical stops outside ordinary noise."""

    engine_version = "3.0.0"

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = _metric_map(result)
        setup = str(metrics.get("setup", "no_trigger"))
        if setup not in _BULLISH_SETUPS or len(context.minute_bars) < 30:
            return _tag(result, gate_passed=False, persistence=False)

        price = _required_decimal(metrics, "reference_price")
        atr14 = _required_decimal(metrics, "atr14")
        momentum = _required_decimal(metrics, "momentum_5_percent")
        persistence = _bullish_persistence(
            context,
            setup,
            _required_decimal(metrics, "session_vwap"),
        )
        higher_low = metrics.get("five_minute_higher_low") is True
        gate_passed = (
            momentum >= MIN_MOMENTUM_PERCENT
            and (persistence or higher_low)
            and str(metrics.get("confirmation_quality", "weak")) in {"standard", "strong"}
        )

        minimum_risk = max(
            price * MIN_RISK_PERCENT / HUNDRED,
            atr14 * MIN_ATR_RISK_MULTIPLE,
        )
        existing_invalidation = _required_decimal(metrics, "invalidation_level")
        existing_risk = price - existing_invalidation
        tactical_risk = max(existing_risk, minimum_risk)
        invalidation = price - tactical_risk
        objective = price + tactical_risk * REWARD_RISK_RATIO
        risk_percent = tactical_risk / price * HUNDRED

        score = result.score if gate_passed else min(result.score, Decimal("64"))
        verdict = result.verdict if gate_passed else AnalysisVerdict.WATCH
        reasons = result.reasons
        if not gate_passed:
            reasons = (*reasons, "v3_confirmation_gate_pending")
        return result.model_copy(
            update={
                "verdict": verdict,
                "score": _score(score),
                "confidence": (_score(score) / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": reasons,
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="invalidation_level", value=_rounded(invalidation)),
                    NamedValue(name="objective_level", value=_rounded(objective)),
                    NamedValue(name="risk_percent", value=_rounded(risk_percent)),
                    NamedValue(name="reward_risk_ratio", value=REWARD_RISK_RATIO),
                    NamedValue(name="risk_ok", value=risk_percent <= Decimal("1.5")),
                    NamedValue(name="confirmation_persistence", value=persistence),
                    NamedValue(name="confirmation_gate_passed", value=gate_passed),
                    NamedValue(name="minimum_tactical_risk", value=_rounded(minimum_risk)),
                    NamedValue(name="entry_confirmation_rule_version", value=self.engine_version),
                ),
            }
        )


def _bullish_persistence(context: IntradayContext, setup: str, session_vwap: Decimal) -> bool:
    latest_two = context.minute_bars[-2:]
    if len(latest_two) < 2:
        return False
    if setup == "bullish_vwap_reclaim":
        return all(bar.close > session_vwap for bar in latest_two)
    earlier = context.minute_bars[-22:-2]
    if not earlier:
        return False
    breakout_level = max(bar.high for bar in earlier)
    return all(bar.close > breakout_level for bar in latest_two)


def _tag(result: AnalysisResult, *, gate_passed: bool, persistence: bool) -> AnalysisResult:
    return result.model_copy(
        update={
            "metrics": _upsert_metrics(
                result,
                NamedValue(name="confirmation_persistence", value=persistence),
                NamedValue(name="confirmation_gate_passed", value=gate_passed),
                NamedValue(name="entry_confirmation_rule_version", value="3.0.0"),
            )
        }
    )


def _metric_map(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _required_decimal(metrics: dict[str, object], name: str) -> Decimal:
    value = metrics.get(name)
    if not isinstance(value, Decimal):
        raise ValueError(f"missing decimal metric: {name}")
    return value


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
