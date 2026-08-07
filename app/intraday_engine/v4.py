"""Price-efficient Intraday v4 confirmation rules preserving v3 for replay."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue

from .models import IntradayContext
from .v3 import IntradayEngineV3

ZERO = Decimal("0")
HUNDRED = Decimal("100")
MAX_TRIGGER_EXTENSION_ATR = Decimal("0.50")
MAX_EMA20_EXTENSION_ATR = Decimal("2.00")
_BULLISH_SETUPS = {"bullish_breakout", "bullish_vwap_reclaim"}


class IntradayEngineV4(IntradayEngineV3):
    """Reject first-impulse entries that no longer offer an efficient price."""

    engine_version = "4.0.0"

    def __init__(
        self,
        *,
        minimum_momentum_percent: Decimal = Decimal("0.15"),
        minimum_risk_percent: Decimal = Decimal("0.25"),
        minimum_atr_risk_multiple: Decimal = Decimal("0.50"),
        reward_risk_ratio: Decimal = Decimal("1.50"),
        maximum_trigger_extension_atr: Decimal = MAX_TRIGGER_EXTENSION_ATR,
        maximum_ema20_extension_atr: Decimal = MAX_EMA20_EXTENSION_ATR,
        strong_confirmation_required: bool = True,
        five_minute_higher_low_required: bool = True,
        strategy_version: str = "4.0.0",
    ) -> None:
        super().__init__(
            minimum_momentum_percent=minimum_momentum_percent,
            minimum_risk_percent=minimum_risk_percent,
            minimum_atr_risk_multiple=minimum_atr_risk_multiple,
            reward_risk_ratio=reward_risk_ratio,
            strategy_version=strategy_version,
        )
        self._maximum_trigger_extension_atr = maximum_trigger_extension_atr
        self._maximum_ema20_extension_atr = maximum_ema20_extension_atr
        self._strong_confirmation_required = strong_confirmation_required
        self._five_minute_higher_low_required = five_minute_higher_low_required

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = _metric_map(result)
        setup = str(metrics.get("setup", "no_trigger"))
        if setup not in _BULLISH_SETUPS:
            return _tag_non_entry(result, strategy_version=self._strategy_version)

        price = _required_decimal(metrics, "reference_price")
        atr14 = _required_decimal(metrics, "atr14")
        ema20 = _required_decimal(metrics, "ema20")
        trigger = _required_decimal(
            metrics,
            "prior_range_high" if setup == "bullish_breakout" else "session_vwap",
        )
        trigger_extension_atr = max(ZERO, (price - trigger) / atr14)
        ema20_extension_atr = max(ZERO, (price - ema20) / atr14)
        entry_window_high = trigger + atr14 * self._maximum_trigger_extension_atr
        efficient = (
            trigger_extension_atr <= self._maximum_trigger_extension_atr
            and ema20_extension_atr <= self._maximum_ema20_extension_atr
        )
        v3_gate = metrics.get("confirmation_gate_passed") is True
        higher_low = (
            not self._five_minute_higher_low_required
            or metrics.get("five_minute_higher_low") is True
        )
        strong = (
            not self._strong_confirmation_required
            or metrics.get("confirmation_quality") == "strong"
        )
        mature_gate = v3_gate and efficient and higher_low and strong

        reasons = list(result.reasons)
        if not efficient:
            reasons.append("late_entry_wait_retest")
        if not higher_low or not strong:
            reasons.append("mature_retest_pending")
        score = result.score if mature_gate else min(result.score, Decimal("64"))
        verdict = result.verdict if mature_gate else AnalysisVerdict.WATCH
        timing = "efficient_retest" if mature_gate else "wait_retest"
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": verdict,
                "score": _score(score),
                "confidence": (_score(score) / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": _unique(tuple(reasons)),
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="raw_v3_confirmation_gate_passed", value=v3_gate),
                    NamedValue(name="confirmation_gate_passed", value=mature_gate),
                    NamedValue(name="entry_efficiency_gate_passed", value=efficient),
                    NamedValue(name="mature_confirmation_gate_passed", value=mature_gate),
                    NamedValue(name="mature_retest_confirmed", value=higher_low and strong),
                    NamedValue(name="entry_trigger_level", value=_rounded(trigger)),
                    NamedValue(name="entry_window_low", value=_rounded(trigger)),
                    NamedValue(name="entry_window_high", value=_rounded(entry_window_high)),
                    NamedValue(
                        name="breakout_extension_atr",
                        value=_rounded(trigger_extension_atr),
                    ),
                    NamedValue(
                        name="ema20_extension_atr",
                        value=_rounded(ema20_extension_atr),
                    ),
                    NamedValue(name="entry_timing", value=timing),
                    NamedValue(
                        name="entry_confirmation_rule_version",
                        value=self._strategy_version,
                    ),
                ),
            }
        )


def _tag_non_entry(result: AnalysisResult, *, strategy_version: str) -> AnalysisResult:
    return result.model_copy(
        update={
            "engine_version": "4.0.0",
            "metrics": _upsert_metrics(
                result,
                NamedValue(name="entry_efficiency_gate_passed", value=False),
                NamedValue(name="mature_confirmation_gate_passed", value=False),
                NamedValue(name="mature_retest_confirmed", value=False),
                NamedValue(name="entry_confirmation_rule_version", value=strategy_version),
            ),
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


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
