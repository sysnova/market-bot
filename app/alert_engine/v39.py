"""Human-only SHORT confirmation alerts over the stable v3.8 policy."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    NamedValue,
    PatternDirection,
)

from .v38 import AlertEngineV38

_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}


class AlertEngineV39(AlertEngineV38):
    """Emit SHORT CONFIRMED after a broken Swing thesis and mature bearish timing."""

    engine_version = "3.9.0"

    def __init__(
        self,
        *args: object,
        short_confirmation_enabled: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._short_confirmation_enabled = short_confirmation_enabled

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
        alert = super().ingest(result, now=now)
        if not self._short_confirmation_enabled:
            return alert
        short = self._confirm_short(
            result.symbol,
            now=now,
            existing=(
                alert
                if alert is not None and alert.kind is AlertKind.BEARISH_CONSENSUS
                else None
            ),
        )
        return short if short is not None else alert

    def _confirm_short(
        self,
        symbol: str,
        *,
        now: datetime,
        existing: LocalAlert | None,
    ) -> LocalAlert | None:
        fresh = self._fresh_values(symbol, now)
        swing = fresh.get(AnalysisHorizon.SWING)
        intraday = fresh.get(AnalysisHorizon.INTRADAY)
        if swing is None or intraday is None:
            return None
        swing_metrics = _metrics(swing)
        intraday_metrics = _metrics(intraday)
        if not (
            swing.direction is PatternDirection.BEARISH
            and swing.verdict in {AnalysisVerdict.CAUTION, AnalysisVerdict.AVOID}
            and swing_metrics.get("short_structure_gate_passed") is True
            and intraday.direction is PatternDirection.BEARISH
            and intraday.verdict is AnalysisVerdict.FAVORABLE
            and intraday_metrics.get("setup") in _BEARISH_SETUPS
            and intraday_metrics.get("short_mature_confirmation_gate_passed") is True
        ):
            return None

        entry = _decimal(intraday_metrics.get("reference_price"))
        invalidation = _decimal(intraday_metrics.get("invalidation_level"))
        target = _decimal(intraday_metrics.get("objective_level"))
        setup_id = swing_metrics.get("short_setup_id")
        rule_version = intraday_metrics.get("short_confirmation_rule_version")
        if not (
            entry is not None
            and invalidation is not None
            and target is not None
            and invalidation > entry > target
            and isinstance(setup_id, str)
            and setup_id
            and isinstance(rule_version, str)
            and rule_version
        ):
            return None

        alert = existing or self._build_named_alert(
            symbol,
            AlertKind.BEARISH_CONSENSUS,
            (swing, intraday),
            fresh,
            now,
        )
        if alert is None:
            return None
        return alert.model_copy(
            update={
                "title": f"{symbol} SHORT CONFIRMED",
                "message": (
                    "Swing marked the prior LONG thesis as broken and mature bearish "
                    "Intraday price action confirmed a human-only SHORT entry; no order "
                    "was submitted"
                ),
                "metrics": _upsert_metrics(
                    alert,
                    NamedValue(name="short_entry_price", value=entry),
                    NamedValue(name="short_invalidation", value=invalidation),
                    NamedValue(name="short_target", value=target),
                    NamedValue(name="short_setup_id", value=setup_id),
                    NamedValue(
                        name="short_confirmation_rule_version",
                        value=rule_version,
                    ),
                ),
                "reasons": tuple(
                    dict.fromkeys(
                        (
                            *alert.reasons,
                            "short_entry_confirmed",
                            "swing_long_thesis_broken",
                            "intraday_bearish_maturity_confirmed",
                            "human_only_no_order_submitted",
                        )
                    )
                ),
            }
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _decimal(value: object) -> Decimal | None:
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
