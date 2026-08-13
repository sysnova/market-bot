"""Fresh options-gamma context with bounded alert score adjustments."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    LocalAlert,
    NamedValue,
)

from .v34 import AlertEngineV34

ZERO = Decimal()
HUNDRED = Decimal("100")
ACTIONABLE_KINDS = {
    AlertKind.SWING_SETUP,
    AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION,
    AlertKind.ENTRY_CONFIRMED,
    AlertKind.HIGH_CONVICTION_BUY,
}


class AlertEngineV35(AlertEngineV34):
    """Use Gamma as optional tactical evidence without changing alert maturity."""

    engine_version = "3.5.0"

    def _build_named_alert(
        self,
        symbol: str,
        kind: AlertKind,
        components: tuple[AnalysisResult, ...],
        fresh: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> LocalAlert | None:
        alert = super()._build_named_alert(symbol, kind, components, fresh, now)
        if alert is None or kind not in ACTIONABLE_KINDS:
            return alert
        gamma = fresh.get(AnalysisHorizon.OPTIONS_GAMMA)
        current_price = _current_price(alert, components)
        context = _gamma_context(gamma, current_price=current_price, now=now)
        if context is None:
            return alert
        delta, reasons = _gamma_delta(context, current_price=current_price)
        effective = min(HUNDRED, max(ZERO, alert.score + delta)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        embedded = tuple(dict.fromkeys((*alert.component_analyses, context)))
        return alert.model_copy(
            update={
                "horizons": tuple(dict.fromkeys((*alert.horizons, context.horizon))),
                "component_analysis_ids": tuple(item.analysis_id for item in embedded),
                "component_analyses": embedded,
                "metrics": (
                    *alert.metrics,
                    NamedValue(name="gamma_base_score", value=alert.score),
                    NamedValue(name="gamma_score_delta", value=delta),
                    NamedValue(name="gamma_effective_score", value=effective),
                    NamedValue(
                        name="gamma_call_wall", value=_metric(context, "gamma_call_wall")
                    ),
                    NamedValue(
                        name="gamma_put_wall", value=_metric(context, "gamma_put_wall")
                    ),
                    NamedValue(
                        name="gamma_max_pain", value=_metric(context, "gamma_max_pain")
                    ),
                ),
                "score": effective,
                "reasons": tuple(dict.fromkeys((*alert.reasons, *reasons))),
            }
        )


def _gamma_context(
    result: AnalysisResult | None, *, current_price: Decimal, now: datetime
) -> AnalysisResult | None:
    if result is None:
        return None
    status = _metric(result, "gamma_status")
    quality = _decimal(_metric(result, "gamma_quality_score"))
    expires_at = _metric(result, "expires_at")
    reference = _decimal(_metric(result, "reference_price"))
    if (
        status != "AVAILABLE"
        or quality is None
        or quality < Decimal("70")
        or not isinstance(expires_at, datetime)
        or expires_at <= now
        or reference is None
        or abs(current_price - reference) / reference * HUNDRED > Decimal("1")
    ):
        return None
    return result


def _gamma_delta(
    result: AnalysisResult, *, current_price: Decimal
) -> tuple[Decimal, tuple[str, ...]]:
    delta = ZERO
    reasons: list[str] = []
    regime = _metric(result, "gamma_regime")
    pin_risk = _metric(result, "gamma_pin_risk") is True
    acceleration = _metric(result, "gamma_acceleration_risk") is True
    call_wall = _decimal(_metric(result, "gamma_call_wall"))
    put_wall = _decimal(_metric(result, "gamma_put_wall"))
    if pin_risk:
        delta -= Decimal("4")
        reasons.append("options_gamma_pin_risk:-4")
    if call_wall is not None and current_price < call_wall and _near(current_price, call_wall, "1"):
        delta -= Decimal("6")
        reasons.append("options_gamma_call_wall_limits_upside:-6")
    if put_wall is not None and current_price >= put_wall and _near(current_price, put_wall, "1"):
        delta += Decimal("4")
        reasons.append("options_gamma_put_wall_support:+4")
    if regime == "NEGATIVE" and acceleration:
        if put_wall is not None and current_price <= put_wall:
            delta -= Decimal("8")
            reasons.append("options_gamma_negative_breakdown_risk:-8")
        elif call_wall is not None and current_price >= call_wall:
            delta += Decimal("3")
            reasons.append("options_gamma_negative_breakout_acceleration:+3")
    return max(Decimal("-10"), min(Decimal("8"), delta)), tuple(reasons)


def _current_price(
    alert: LocalAlert, components: tuple[AnalysisResult, ...]
) -> Decimal:
    for name in ("entry_price", "current_price", "reference_price"):
        value = _decimal(next((item.value for item in alert.metrics if item.name == name), None))
        if value is not None:
            return value
    for result in reversed(components):
        value = _decimal(_metric(result, "reference_price"))
        if value is not None:
            return value
    raise ValueError("actionable alert has no reference price for Gamma context")


def _metric(result: AnalysisResult, name: str) -> object | None:
    return next((item.value for item in result.metrics if item.name == name), None)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > ZERO else None


def _near(price: Decimal, level: Decimal, percent: str) -> bool:
    return abs(price - level) / level * HUNDRED <= Decimal(percent)
