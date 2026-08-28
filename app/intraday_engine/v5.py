"""Symmetric mature SHORT confirmation preserving Intraday v4 for rollback."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue

from .models import IntradayContext
from .v4 import IntradayEngineV4

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}


class IntradayEngineV5(IntradayEngineV4):
    """Confirm efficient bearish continuation with persistence and lower-high structure."""

    engine_version = "5.0.0"

    def __init__(
        self,
        *,
        short_confirmation_enabled: bool = True,
        five_minute_lower_high_required: bool = True,
        short_ema20_extension_required: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._short_confirmation_enabled = short_confirmation_enabled
        self._five_minute_lower_high_required = five_minute_lower_high_required
        self._short_ema20_extension_required = short_ema20_extension_required

    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        metrics = _metrics(result)
        setup = str(metrics.get("setup", "no_trigger"))
        if not self._short_confirmation_enabled or setup not in _BEARISH_SETUPS:
            return _tag_non_short(
                result,
                strategy_version=self._strategy_version,
                engine_version=self.engine_version,
            )

        price = _required_decimal(metrics, "reference_price")
        atr14 = _required_decimal(metrics, "atr14")
        ema20 = _required_decimal(metrics, "ema20")
        session_vwap = _required_decimal(metrics, "session_vwap")
        momentum = _required_decimal(metrics, "momentum_5_percent")
        trigger = _required_decimal(
            metrics,
            "prior_range_low" if setup == "bearish_breakdown" else "session_vwap",
        )
        persistence = _bearish_persistence(
            context,
            setup=setup,
            session_vwap=session_vwap,
        )
        lower_high = _five_minute_lower_high(context)
        quality = str(metrics.get("confirmation_quality", "weak"))
        raw_gate = (
            momentum <= -self._minimum_momentum_percent
            and (persistence or lower_high)
            and quality in {"standard", "strong"}
        )

        existing_invalidation = _required_decimal(metrics, "invalidation_level")
        minimum_risk = max(
            price * self._minimum_risk_percent / HUNDRED,
            atr14 * self._minimum_atr_risk_multiple,
        )
        tactical_risk = max(existing_invalidation - price, minimum_risk)
        invalidation = price + tactical_risk
        objective = max(
            Decimal("0.0001"),
            price - tactical_risk * self._reward_risk_ratio,
        )
        risk_percent = tactical_risk / price * HUNDRED
        risk_ok = risk_percent <= Decimal("1.5")

        trigger_extension_atr = max(ZERO, (trigger - price) / atr14)
        ema20_extension_atr = max(ZERO, (ema20 - price) / atr14)
        entry_window_low = max(
            Decimal("0.0001"),
            trigger - atr14 * self._maximum_trigger_extension_atr,
        )
        efficient = (
            trigger_extension_atr <= self._maximum_trigger_extension_atr
            and (
                ema20_extension_atr <= self._maximum_ema20_extension_atr
                or not self._short_ema20_extension_required
            )
        )
        structure_confirmed = (
            lower_high or not self._five_minute_lower_high_required
        )
        strong = (
            quality == "strong" or not self._strong_confirmation_required
        )
        mature_gate = raw_gate and efficient and structure_confirmed and strong and risk_ok

        reasons = list(result.reasons)
        if not efficient:
            reasons.append("short_late_entry_wait_retest")
        if not structure_confirmed or not strong:
            reasons.append("short_mature_retest_pending")
        if not raw_gate:
            reasons.append("short_confirmation_gate_pending")
        score = result.score if mature_gate else min(result.score, Decimal("64"))
        verdict = result.verdict if mature_gate else AnalysisVerdict.WATCH
        timing = "efficient_lower_high" if mature_gate else "wait_short_retest"
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "verdict": verdict,
                "score": _score(score),
                "confidence": (_score(score) / HUNDRED).quantize(Decimal("0.0001")),
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": _upsert_metrics(
                    result,
                    NamedValue(name="invalidation_level", value=_rounded(invalidation)),
                    NamedValue(name="objective_level", value=_rounded(objective)),
                    NamedValue(name="risk_percent", value=_rounded(risk_percent)),
                    NamedValue(name="risk_ok", value=risk_ok),
                    NamedValue(name="minimum_tactical_risk", value=_rounded(minimum_risk)),
                    NamedValue(name="five_minute_lower_high", value=lower_high),
                    NamedValue(name="short_confirmation_persistence", value=persistence),
                    NamedValue(name="short_confirmation_gate_passed", value=raw_gate),
                    NamedValue(
                        name="short_entry_efficiency_gate_passed",
                        value=efficient,
                    ),
                    NamedValue(
                        name="short_mature_confirmation_gate_passed",
                        value=mature_gate,
                    ),
                    NamedValue(
                        name="short_mature_retest_confirmed",
                        value=structure_confirmed and strong,
                    ),
                    NamedValue(name="short_entry_trigger_level", value=_rounded(trigger)),
                    NamedValue(name="short_entry_window_low", value=_rounded(entry_window_low)),
                    NamedValue(name="short_entry_window_high", value=_rounded(trigger)),
                    NamedValue(
                        name="short_breakdown_extension_atr",
                        value=_rounded(trigger_extension_atr),
                    ),
                    NamedValue(
                        name="short_ema20_extension_atr",
                        value=_rounded(ema20_extension_atr),
                    ),
                    NamedValue(name="short_entry_timing", value=timing),
                    NamedValue(
                        name="short_confirmation_rule_version",
                        value=self._strategy_version,
                    ),
                    NamedValue(name="raw_v3_confirmation_gate_passed", value=raw_gate),
                    NamedValue(name="confirmation_persistence", value=persistence),
                    NamedValue(name="confirmation_gate_passed", value=mature_gate),
                    NamedValue(name="entry_efficiency_gate_passed", value=efficient),
                    NamedValue(name="mature_confirmation_gate_passed", value=mature_gate),
                    NamedValue(
                        name="mature_retest_confirmed",
                        value=structure_confirmed and strong,
                    ),
                    NamedValue(name="entry_trigger_level", value=_rounded(trigger)),
                    NamedValue(name="entry_window_low", value=_rounded(entry_window_low)),
                    NamedValue(name="entry_window_high", value=_rounded(trigger)),
                    NamedValue(name="entry_timing", value=timing),
                    NamedValue(
                        name="entry_confirmation_rule_version",
                        value=self._strategy_version,
                    ),
                ),
            }
        )


def _bearish_persistence(
    context: IntradayContext,
    *,
    setup: str,
    session_vwap: Decimal,
) -> bool:
    latest_two = context.minute_bars[-2:]
    if len(latest_two) < 2:
        return False
    if setup == "bearish_vwap_rejection":
        return all(bar.close < session_vwap for bar in latest_two)
    earlier = context.minute_bars[-22:-2]
    if not earlier:
        return False
    breakdown_level = min(bar.low for bar in earlier)
    return all(bar.close < breakdown_level for bar in latest_two)


def _five_minute_lower_high(context: IntradayContext) -> bool:
    bars = context.five_minute_bars
    if len(bars) < 6:
        return False
    previous_ceiling = max(bar.high for bar in bars[-6:-3])
    current_ceiling = max(bar.high for bar in bars[-3:])
    return current_ceiling < previous_ceiling and bars[-1].close < bars[-2].close


def _tag_non_short(
    result: AnalysisResult,
    *,
    strategy_version: str,
    engine_version: str,
) -> AnalysisResult:
    return result.model_copy(
        update={
            "engine_version": engine_version,
            "metrics": _upsert_metrics(
                result,
                NamedValue(name="five_minute_lower_high", value=False),
                NamedValue(name="short_confirmation_persistence", value=False),
                NamedValue(name="short_confirmation_gate_passed", value=False),
                NamedValue(name="short_entry_efficiency_gate_passed", value=False),
                NamedValue(name="short_mature_confirmation_gate_passed", value=False),
                NamedValue(name="short_mature_retest_confirmed", value=False),
                NamedValue(
                    name="short_confirmation_rule_version",
                    value=strategy_version,
                ),
            ),
        }
    )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _required_decimal(metrics: dict[str, object], name: str) -> Decimal:
    value = metrics.get(name)
    if not isinstance(value, Decimal):
        raise ValueError(f"missing decimal metric: {name}")
    return value


def _rounded(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)
