"""Alert-owned quality decisions for source-agnostic entry setup assessments."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    EntryMaturityLevel,
    EntrySetupAssessment,
    EntrySignalFamily,
    LocalAlert,
    NamedValue,
)

from .policy import AlertPolicy
from .state import AlertEngineV3State
from .v31 import AlertEngineV31


class AlertEngineV32(AlertEngineV31):
    """Decide recovery quality inside Alert without knowing producer versions."""

    engine_version = "3.2.0"

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
        restored_state: AlertEngineV3State | None = None,
    ) -> None:
        super().__init__(
            policy,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
            same_market_session_required=same_market_session_required,
            restored_state=restored_state,
        )
        if not recovery_required_horizons:
            raise ValueError("recovery_required_horizons must not be empty")
        if len(recovery_required_horizons) != len(set(recovery_required_horizons)):
            raise ValueError("recovery_required_horizons must be unique")
        self._recovery_required_horizons = recovery_required_horizons
        self._recovery_maturity = recovery_maturity

    def ingest_setup_assessment(
        self,
        assessment: EntrySetupAssessment,
        *,
        now: datetime,
    ) -> LocalAlert | None:
        """Accept recovery evidence and assign its Alert-owned core maturity."""

        if assessment.assessed_at > now:
            raise ValueError("entry setup assessment cannot be in the future")
        if assessment.family is not EntrySignalFamily.CORE_RECOVERY:
            return None
        by_horizon = {item.horizon: item for item in assessment.component_analyses}
        components = tuple(
            by_horizon.get(horizon) for horizon in self._recovery_required_horizons
        )
        if any(item is None for item in components):
            return None
        selected = tuple(item for item in components if item is not None)
        for result in selected:
            if now - result.as_of > self._policy.for_horizon(result.horizon).max_age:
                return None

        deduplication_key = (
            f"alert:v3.2:core-recovery:{assessment.setup_id}:"
            f"{self._recovery_maturity.value.lower()}"
        )
        if deduplication_key in self._emitted_keys:
            return None
        weighted = sum(
            (
                self._policy.for_horizon(item.horizon).weight
                * item.score
                * item.confidence
                for item in selected
            ),
            Decimal(),
        )
        total_weight = sum(
            (self._policy.for_horizon(item.horizon).weight for item in selected),
            Decimal(),
        )
        score = (weighted / total_weight).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        metrics = [
            NamedValue(name="entry_price", value=assessment.entry_price),
            NamedValue(name="entry_signal_family", value=assessment.family.value),
            NamedValue(name="entry_maturity", value=self._recovery_maturity.value),
            NamedValue(name="entry_setup_id", value=assessment.setup_id),
            NamedValue(
                name="entry_setup_policy_version",
                value=assessment.policy_version,
            ),
        ]
        if assessment.zone_low is not None:
            metrics.extend(
                (
                    NamedValue(name="buy_zone_low", value=assessment.zone_low),
                    NamedValue(name="buy_zone_high", value=assessment.zone_high),
                    NamedValue(name="invalidation", value=assessment.invalidation),
                )
            )
        for index, target in enumerate(assessment.targets, start=1):
            metrics.append(
                NamedValue(name="target" if index == 1 else f"target_{index}", value=target)
            )
        alert = LocalAlert(
            alert_id=assessment.assessment_id,
            symbol=assessment.symbol,
            created_at=now,
            kind=AlertKind.ENTRY_CONFIRMED,
            severity=AlertSeverity.ACTION,
            title=(
                f"{assessment.symbol} CORE RECOVERY "
                f"{self._recovery_maturity.value} CONFIRMED"
            ),
            message=(
                "Alert accepted the recovered setup with fresh Swing and Intraday evidence"
            ),
            horizons=self._recovery_required_horizons,
            component_analysis_ids=tuple(item.analysis_id for item in selected),
            component_analyses=selected,
            metrics=tuple(metrics),
            score=score,
            reasons=tuple(
                dict.fromkeys(
                    (
                        "core_recovery_confirmed",
                        f"alert_maturity_{self._recovery_maturity.value.lower()}",
                        *assessment.reasons,
                    )
                )
            ),
            deduplication_key=deduplication_key,
            expires_at=now + self._policy.alert_ttl,
        )
        self._emitted_keys.add(deduplication_key)
        return alert
