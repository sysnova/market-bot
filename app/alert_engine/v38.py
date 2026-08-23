"""Direct L2 promotion for an independently confirmed Swing recovery."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    EntryMaturityLevel,
    EntrySignalFamily,
    LocalAlert,
    NamedValue,
)

from .policy import AlertPolicy
from .state import AlertEngineV3State
from .v37 import AlertEngineV37


class AlertEngineV38(AlertEngineV37):
    """Promote each fresh correction-anchored recovery exactly once at L2."""

    engine_version = "3.8.0"

    def __init__(
        self,
        policy: AlertPolicy | None = None,
        *,
        minimum_reconfirmation_delay: timedelta = timedelta(minutes=3),
        strong_confirmation_required: bool = True,
        five_minute_higher_low_required: bool = True,
        same_market_session_required: bool = True,
        recovery_required_horizons: tuple[AnalysisHorizon, ...] = (
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        ),
        recovery_maturity: EntryMaturityLevel = EntryMaturityLevel.L2,
        minimum_swing_reward_risk_to_resistance: Decimal = Decimal("1.50"),
        intraday_mature_gate_required: bool = True,
        direct_swing_recovery_l2: bool = True,
        restored_state: AlertEngineV3State | None = None,
    ) -> None:
        super().__init__(
            policy,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
            same_market_session_required=same_market_session_required,
            recovery_required_horizons=recovery_required_horizons,
            recovery_maturity=recovery_maturity,
            minimum_swing_reward_risk_to_resistance=(minimum_swing_reward_risk_to_resistance),
            intraday_mature_gate_required=intraday_mature_gate_required,
            restored_state=restored_state,
        )
        self._direct_swing_recovery_l2 = direct_swing_recovery_l2
        self._confirmed_recovery_setups: set[str] = set()

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
        alert = super().ingest(result, now=now)
        if result.horizon is AnalysisHorizon.SWING and self._direct_swing_recovery_l2:
            recovery = self._confirm_structure_recovery(result, intraday=None, now=now)
            return recovery if recovery is not None else alert
        if alert is not None:
            return alert
        if result.horizon is AnalysisHorizon.INTRADAY:
            swing = self._latest.get(result.symbol, {}).get(AnalysisHorizon.SWING)
            if swing is not None:
                return self._confirm_structure_recovery(
                    swing,
                    intraday=result,
                    now=now,
                )
        return None

    def _confirm_structure_recovery(
        self,
        swing: AnalysisResult,
        *,
        intraday: AnalysisResult | None,
        now: datetime,
    ) -> LocalAlert | None:
        if now - swing.as_of > self._policy.for_horizon(AnalysisHorizon.SWING).max_age:
            return None
        swing_metrics = _metrics(swing)
        reward_risk = _decimal(swing_metrics.get("reward_risk_to_resistance"))
        if not (
            swing_metrics.get("classification") == "recovery"
            and swing_metrics.get("entry_lane") == "STRUCTURE_RECOVERY"
            and swing_metrics.get("recovery_entry_gate_passed") is True
            and swing_metrics.get("swing_entry_gate_passed") is True
            and reward_risk is not None
            and reward_risk >= self._minimum_swing_reward_risk_to_resistance
        ):
            return None
        if intraday is not None and not self._qualifies(intraday):
            return None
        setup_id = swing_metrics.get("recovery_setup_id")
        if not isinstance(setup_id, str) or not setup_id:
            return None
        if setup_id in self._confirmed_recovery_setups:
            return None

        fresh = self._fresh_values(swing.symbol, now)
        components = (swing,) if intraday is None else (swing, intraday)
        alert = self._build_named_alert(
            swing.symbol,
            AlertKind.ENTRY_CONFIRMED,
            components,
            fresh,
            now,
        )
        if alert is None:
            return None
        price_source = swing if intraday is None else intraday
        entry_price = _decimal(_metrics(price_source).get("reference_price"))
        recovery_avwap = _decimal(swing_metrics.get("recovery_avwap"))
        invalidation = _decimal(swing_metrics.get("invalidation"))
        if (
            entry_price is None
            or recovery_avwap is None
            or invalidation is None
            or invalidation >= min(entry_price, recovery_avwap)
        ):
            return None

        self._confirmed_recovery_setups.add(setup_id)
        deduplication_key = f"alert:v3.8:core-recovery:{setup_id}:l2"
        self._emitted_keys.add(deduplication_key)
        return alert.model_copy(
            update={
                "title": f"{swing.symbol} STRUCTURE RECOVERY L2 CONFIRMED",
                "message": (
                    "A fresh correction-anchored Swing recovery and mature Intraday "
                    "price action confirm an L2 analytical entry"
                ),
                "metrics": _upsert_metrics(
                    alert,
                    NamedValue(name="entry_price", value=entry_price),
                    NamedValue(
                        name="entry_signal_family",
                        value=EntrySignalFamily.CORE_RECOVERY.value,
                    ),
                    NamedValue(name="entry_maturity", value="L2"),
                    NamedValue(name="entry_setup_id", value=setup_id),
                    NamedValue(name="entry_setup_policy_version", value="1.3.0"),
                    NamedValue(
                        name="buy_zone_low",
                        value=min(entry_price, recovery_avwap),
                    ),
                    NamedValue(name="buy_zone_high", value=entry_price),
                    NamedValue(name="invalidation", value=invalidation),
                ),
                "reasons": tuple(
                    dict.fromkeys(
                        (
                            *alert.reasons,
                            "swing_recovery_l2_confirmed",
                            "fresh_correction_anchor",
                            "swing_embedded_intraday_maturity",
                            "recovery_setup_deduplicated",
                        )
                    )
                ),
                "deduplication_key": deduplication_key,
            }
        )


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except ValueError, TypeError:
        return None
    return parsed if parsed.is_finite() else None


def _upsert_metrics(alert: LocalAlert, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in alert.metrics if item.name not in names), *items)
