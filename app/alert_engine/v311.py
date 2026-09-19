"""Protect confirmed SHORT alerts from structural-support bounce risk."""

from datetime import datetime
from decimal import Decimal

from app.contracts import AlertSeverity, AnalysisHorizon, LocalAlert, NamedValue

from .v310 import AlertEngineV310

_CONFIRMATION_REASONS = {
    "short_entry_confirmed",
    "intraday_bearish_maturity_confirmed",
    "human_only_no_order_submitted",
}


class AlertEngineV311(AlertEngineV310):
    """Require room to support or a decisive support break before SHORT entry."""

    engine_version = "3.11.0"

    def __init__(
        self,
        *args: object,
        short_support_guard_enabled: bool = True,
        short_minimum_support_distance_atr: Decimal = Decimal("0.50"),
        short_support_break_clearance_atr: Decimal = Decimal("0.25"),
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        if (
            short_minimum_support_distance_atr <= 0
            or short_support_break_clearance_atr <= 0
        ):
            raise ValueError("SHORT support guard thresholds must be positive")
        self._short_support_guard_enabled = short_support_guard_enabled
        self._short_minimum_support_distance_atr = short_minimum_support_distance_atr
        self._short_support_break_clearance_atr = short_support_break_clearance_atr

    def _confirm_short(
        self, symbol: str, *, now: datetime, existing: LocalAlert | None
    ) -> LocalAlert | None:
        alert = super()._confirm_short(symbol, now=now, existing=existing)
        if alert is None or not self._short_support_guard_enabled:
            return alert

        swing = next(
            (
                item
                for item in alert.component_analyses
                if item.horizon is AnalysisHorizon.SWING
            ),
            None,
        )
        swing_metrics = (
            {item.name: item.value for item in swing.metrics} if swing is not None else {}
        )
        alert_metrics = {item.name: item.value for item in alert.metrics}
        entry = _positive_decimal(alert_metrics.get("short_entry_price"))
        support = _positive_decimal(
            swing_metrics.get("structural_support", swing_metrics.get("support"))
        )
        atr = _positive_decimal(swing_metrics.get("atr14"))
        if entry is None or support is None or atr is None:
            return self._blocked(
                alert,
                title=f"{symbol} SHORT BLOCKED - SUPPORT DATA",
                reason="short_support_data_missing",
                distance=None,
                support=support,
            )

        distance = ((entry - support) / atr).quantize(Decimal("0.0001"))
        passed = (
            distance >= self._short_minimum_support_distance_atr
            or distance <= -self._short_support_break_clearance_atr
        )
        metrics = _upsert_metrics(
            alert,
            NamedValue(name="short_support_guard_passed", value=passed),
            NamedValue(name="short_structural_support", value=support),
            NamedValue(name="short_support_distance_atr", value=distance),
            NamedValue(
                name="short_minimum_support_distance_atr",
                value=self._short_minimum_support_distance_atr,
            ),
            NamedValue(
                name="short_support_break_clearance_atr",
                value=self._short_support_break_clearance_atr,
            ),
        )
        if passed:
            return alert.model_copy(update={"metrics": metrics})
        return self._blocked(
            alert.model_copy(update={"metrics": metrics}),
            title=f"{symbol} SHORT BLOCKED - SUPPORT",
            reason="short_confirmation_blocked_near_support",
            distance=distance,
            support=support,
        )

    def _blocked(
        self,
        alert: LocalAlert,
        *,
        title: str,
        reason: str,
        distance: Decimal | None,
        support: Decimal | None,
    ) -> LocalAlert:
        metrics = _upsert_metrics(
            alert,
            NamedValue(name="short_support_guard_passed", value=False),
            NamedValue(
                name="short_minimum_support_distance_atr",
                value=self._short_minimum_support_distance_atr,
            ),
            NamedValue(
                name="short_support_break_clearance_atr",
                value=self._short_support_break_clearance_atr,
            ),
            *(
                (NamedValue(name="short_structural_support", value=support),)
                if support is not None
                else ()
            ),
            *(
                (NamedValue(name="short_support_distance_atr", value=distance),)
                if distance is not None
                else ()
            ),
        )
        reasons = [item for item in alert.reasons if item not in _CONFIRMATION_REASONS]
        reasons.extend((reason, "short_support_bounce_risk"))
        return alert.model_copy(
            update={
                "severity": AlertSeverity.WATCH,
                "title": title,
                "message": (
                    "Bearish timing was confirmed, but the SHORT entry was blocked because "
                    "structural support can produce an adverse rebound"
                ),
                "reasons": tuple(dict.fromkeys(reasons)),
                "metrics": metrics,
            }
        )


def _positive_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _upsert_metrics(alert: LocalAlert, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in alert.metrics if item.name not in names), *items)
