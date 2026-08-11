"""Entry-actionability gates and early Intraday monitoring alerts."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryMaturityLevel,
    LocalAlert,
    NamedValue,
    PatternDirection,
)

from .policy import AlertPolicy
from .state import AlertEngineV3State
from .v32 import AlertEngineV32

_BUY_KINDS = {AlertKind.ENTRY_CONFIRMED, AlertKind.HIGH_CONVICTION_BUY}
_BULLISH_INTRADAY_SETUPS = {
    "bullish_breakout",
    "bullish_vwap_reclaim",
    "bullish_entry_confirmation",
}


class AlertEngineV33(AlertEngineV32):
    """Separate bullish structure from a price-actionable entry."""

    engine_version = "3.3.0"

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
            restored_state=restored_state,
        )
        if minimum_swing_reward_risk_to_resistance <= 0:
            raise ValueError(
                "minimum_swing_reward_risk_to_resistance must be positive"
            )
        self._minimum_swing_reward_risk_to_resistance = (
            minimum_swing_reward_risk_to_resistance
        )
        self._intraday_mature_gate_required = intraday_mature_gate_required

    def _select_alert(
        self,
        incoming: AnalysisResult,
        fresh: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[AlertKind, tuple[AnalysisResult, ...]] | None:
        selected = super()._select_alert(incoming, fresh)
        if selected is not None and selected[0] is AlertKind.SWING_SETUP:
            swing = next(
                (
                    item
                    for item in selected[1]
                    if item.horizon is AnalysisHorizon.SWING
                ),
                None,
            )
            if swing is None or not self._swing_entry_actionable(swing):
                return None
        if selected is not None and selected[0] in _BUY_KINDS:
            if self._confirmation_gates_pass(selected[1]):
                return selected
            early = self._early_intraday_components(incoming, fresh)
            return (
                (AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION, early)
                if early is not None
                else None
            )
        early = self._early_intraday_components(incoming, fresh)
        if early is not None:
            return AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION, early
        return selected

    def _build_named_alert(
        self,
        symbol: str,
        kind: AlertKind,
        components: tuple[AnalysisResult, ...],
        fresh: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> LocalAlert | None:
        alert = super()._build_named_alert(symbol, kind, components, fresh, now)
        if alert is None or kind is not AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION:
            return alert
        return alert.model_copy(
            update={
                "title": f"{symbol} EARLY INTRADAY WITHOUT CONFIRMATION",
                "message": (
                    "Bullish Intraday structure is visible, but entry actionability "
                    "or mature confirmation is still missing"
                ),
                "metrics": _early_metrics(components),
                "reasons": tuple(
                    dict.fromkeys(
                        (
                            *alert.reasons,
                            "manual_monitoring_only",
                            "core_entry_not_confirmed",
                        )
                    )
                ),
            }
        )

    def _valid_swing(self, result: AnalysisResult) -> bool:
        return super()._valid_swing(result) and self._swing_entry_actionable(result)

    def _qualifies(self, result: AnalysisResult) -> bool:
        if not super()._qualifies(result):
            return False
        if not self._intraday_mature_gate_required:
            return True
        return _metrics(result).get("mature_confirmation_gate_passed") is True

    def _confirmation_gates_pass(
        self, components: tuple[AnalysisResult, ...]
    ) -> bool:
        by_horizon = {item.horizon: item for item in components}
        intraday = by_horizon.get(AnalysisHorizon.INTRADAY)
        if intraday is None:
            return False
        if (
            self._intraday_mature_gate_required
            and _metrics(intraday).get("mature_confirmation_gate_passed") is not True
        ):
            return False
        swing = by_horizon.get(AnalysisHorizon.SWING)
        return swing is None or self._swing_entry_actionable(swing)

    def _swing_entry_actionable(self, result: AnalysisResult) -> bool:
        metrics = _metrics(result)
        if metrics.get("swing_entry_gate_passed") is not True:
            return False
        reward_risk = metrics.get("reward_risk_to_resistance")
        return (
            isinstance(reward_risk, Decimal)
            and reward_risk >= self._minimum_swing_reward_risk_to_resistance
        )

    def _early_intraday_components(
        self,
        incoming: AnalysisResult,
        fresh: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[AnalysisResult, ...] | None:
        if incoming.horizon is not AnalysisHorizon.INTRADAY:
            return None
        metrics = _metrics(incoming)
        if (
            incoming.direction is not PatternDirection.BULLISH
            or incoming.verdict not in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}
            or metrics.get("setup") not in _BULLISH_INTRADAY_SETUPS
            or metrics.get("confirmation_quality") != "strong"
            or metrics.get("five_minute_higher_low") is not True
        ):
            return None
        swing = fresh.get(AnalysisHorizon.SWING)
        long_term = fresh.get(AnalysisHorizon.LONG_TERM)
        structural_swing = swing if swing is not None and _swing_structure_valid(swing) else None
        structural_long = (
            long_term if long_term is not None and _long_structure_valid(long_term) else None
        )
        if structural_swing is None and structural_long is None:
            return None
        mature = metrics.get("mature_confirmation_gate_passed") is True
        swing_actionable = (
            structural_swing is None or self._swing_entry_actionable(structural_swing)
        )
        if mature and swing_actionable:
            return None
        return tuple(
            item
            for item in (structural_long, structural_swing, incoming)
            if item is not None
        )


def _swing_structure_valid(result: AnalysisResult) -> bool:
    metrics = _metrics(result)
    return (
        result.direction is PatternDirection.BULLISH
        and result.verdict in {
            AnalysisVerdict.FAVORABLE,
            AnalysisVerdict.WATCH,
            AnalysisVerdict.CAUTION,
        }
        and metrics.get("structure_broken_confirmed") is not True
        and metrics.get("anchored_vwap_gate_passed") is True
        and metrics.get("classification") in {"pullback", "breakout", "extended"}
    )


def _long_structure_valid(result: AnalysisResult) -> bool:
    metrics = _metrics(result)
    return (
        result.direction is PatternDirection.BULLISH
        and result.verdict in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}
        and metrics.get("classification") != "extended"
    )


def _early_metrics(components: tuple[AnalysisResult, ...]) -> tuple[NamedValue, ...]:
    by_horizon = {item.horizon: item for item in components}
    intraday = by_horizon.get(AnalysisHorizon.INTRADAY)
    swing = by_horizon.get(AnalysisHorizon.SWING)
    output: list[NamedValue] = []
    for name, source_name, source in (
        ("current_price", "reference_price", intraday),
        ("entry_trigger_level", "entry_trigger_level", intraday),
        ("resistance", "resistance", swing),
        ("invalidation", "invalidation", swing),
        (
            "swing_reward_risk_to_resistance",
            "reward_risk_to_resistance",
            swing,
        ),
    ):
        if source is None:
            continue
        value = _metrics(source).get(source_name)
        if value is not None:
            output.append(NamedValue(name=name, value=value))
    return tuple(output)


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
