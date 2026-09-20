"""V1.3: own the underlying SHORT decision before inverse-instrument timing."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.common.market_session import is_regular_session
from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    NamedValue,
    PatternDirection,
    SupportAssessment,
    SupportState,
)

from .engine import _stable_uuid7  # pyright: ignore[reportPrivateUsage]
from .v12 import LeveragedThesisEngineV12

_BEARISH_SETUPS = {"bearish_breakdown", "bearish_vwap_rejection"}
_INACTIVE_SUPPORT_STATES = {
    SupportState.EXPIRED,
    SupportState.INVALIDATED,
    SupportState.NO_KEY_SUPPORT,
    SupportState.NO_NEARBY_SUPPORT,
}


class LeveragedThesisEngineV13(LeveragedThesisEngineV12):
    """Confirm scoped SHORTs and hand the retained intent to inverse instruments."""

    engine_version = "1.3.0"

    def __init__(
        self,
        *args: object,
        short_minimum_support_distance_atr: Decimal = Decimal("0.50"),
        short_support_break_clearance_atr: Decimal = Decimal("0.25"),
        short_maximum_intraday_age: timedelta = timedelta(minutes=2),
        short_maximum_swing_age: timedelta = timedelta(hours=8),
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        if (
            short_minimum_support_distance_atr <= 0
            or short_support_break_clearance_atr <= 0
            or short_maximum_intraday_age <= timedelta()
            or short_maximum_swing_age <= timedelta()
        ):
            raise ValueError("SHORT decision thresholds must be positive")
        self._short_minimum_support_distance_atr = short_minimum_support_distance_atr
        self._short_support_break_clearance_atr = short_support_break_clearance_atr
        self._short_maximum_intraday_age = short_maximum_intraday_age
        self._short_maximum_swing_age = short_maximum_swing_age

    def evaluate_short(
        self,
        *,
        swing: AnalysisResult,
        intraday: AnalysisResult,
        now: datetime,
        support: SupportAssessment | None = None,
    ) -> LocalAlert | None:
        """Return the one human SHORT decision for a leveraged pair underlying."""

        if (
            swing.symbol != intraday.symbol
            or self.pair_for_underlying(swing.symbol) is None
            or swing.horizon is not AnalysisHorizon.SWING
            or intraday.horizon is not AnalysisHorizon.INTRADAY
            or swing.as_of > now
            or intraday.as_of > now
            or now - swing.as_of > self._short_maximum_swing_age
            or now - intraday.as_of > self._short_maximum_intraday_age
            or not is_regular_session(now)
            or not is_regular_session(intraday.as_of)
        ):
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

        entry = _positive_decimal(intraday_metrics.get("reference_price"))
        invalidation = _positive_decimal(intraday_metrics.get("invalidation_level"))
        target = _positive_decimal(intraday_metrics.get("objective_level"))
        atr = _positive_decimal(swing_metrics.get("atr14"))
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

        matching_support = (
            support if support is not None and support.symbol == swing.symbol else None
        )
        support_level = self._nearest_support(
            entry=entry,
            swing_metrics=swing_metrics,
            support=matching_support,
            now=now,
        )
        distance = (
            ((entry - support_level) / atr).quantize(Decimal("0.0001"))
            if support_level is not None and atr is not None
            else None
        )
        support_passed = distance is not None and (
            distance >= self._short_minimum_support_distance_atr
            or distance <= -self._short_support_break_clearance_atr
        )
        blocked_reason = (
            None
            if support_passed
            else (
                "short_support_data_missing"
                if distance is None
                else "short_confirmation_blocked_near_support"
            )
        )
        decision = "confirmed" if support_passed else "blocked"
        alert_id = _stable_uuid7(
            swing.as_of,
            f"leveraged-thesis:v1.3:short:{decision}:{setup_id}",
        )
        reasons = (
            (
                "short_entry_confirmed",
                "swing_long_thesis_broken",
                "intraday_bearish_maturity_confirmed",
                "short_decision_owned_by_leveraged_thesis",
                "human_only_no_order_submitted",
            )
            if support_passed
            else (
                blocked_reason,
                "short_support_bounce_risk",
                "short_decision_owned_by_leveraged_thesis",
            )
        )
        assert all(reason is not None for reason in reasons)
        metrics = (
            NamedValue(name="short_entry_price", value=entry),
            NamedValue(name="short_invalidation", value=invalidation),
            NamedValue(name="short_target", value=target),
            NamedValue(name="short_setup_id", value=setup_id),
            NamedValue(name="short_confirmation_rule_version", value=rule_version),
            NamedValue(name="short_support_guard_passed", value=support_passed),
            NamedValue(name="short_structural_support", value=support_level),
            NamedValue(name="short_support_distance_atr", value=distance),
            NamedValue(
                name="short_minimum_support_distance_atr",
                value=self._short_minimum_support_distance_atr,
            ),
            NamedValue(
                name="short_support_break_clearance_atr",
                value=self._short_support_break_clearance_atr,
            ),
            NamedValue(
                name="short_support_assessment_id",
                value=(matching_support.assessment_id if matching_support is not None else None),
            ),
        )
        return LocalAlert(
            alert_id=alert_id,
            symbol=swing.symbol,
            created_at=now,
            expires_at=now + timedelta(minutes=15),
            severity=AlertSeverity.ACTION if support_passed else AlertSeverity.WATCH,
            title=(
                f"{swing.symbol} SHORT CONFIRMED"
                if support_passed
                else (
                    f"{swing.symbol} SHORT BLOCKED - SUPPORT DATA"
                    if distance is None
                    else f"{swing.symbol} SHORT BLOCKED - SUPPORT"
                )
            ),
            message=(
                "Swing structure and fresh bearish Intraday timing confirmed a human-only "
                "SHORT entry with sufficient room from structural support; no order was submitted"
                if support_passed
                else "Bearish timing was confirmed, but the SHORT entry was blocked because "
                "structural support can produce an adverse rebound"
            ),
            horizons=(AnalysisHorizon.SWING, AnalysisHorizon.INTRADAY),
            component_analysis_ids=(swing.analysis_id, intraday.analysis_id),
            component_analyses=(swing, intraday),
            metrics=metrics,
            score=min(swing.score, intraday.score),
            reasons=tuple(reason for reason in reasons if reason is not None),
            deduplication_key=f"leveraged-thesis:v1.3:short:{decision}:{setup_id}",
            kind=AlertKind.BEARISH_CONSENSUS,
        )

    def _nearest_support(
        self,
        *,
        entry: Decimal,
        swing_metrics: dict[str, object],
        support: SupportAssessment | None,
        now: datetime,
    ) -> Decimal | None:
        levels: list[Decimal] = []
        swing_support = _positive_decimal(
            swing_metrics.get("structural_support", swing_metrics.get("support"))
        )
        if swing_support is not None:
            levels.append(swing_support)
        if support is not None:
            assessed_at = support.assessed_at or support.occurred_at
            support_is_fresh = (
                assessed_at <= now
                and now - assessed_at <= self._short_maximum_swing_age
            )
            if support_is_fresh and support.state not in _INACTIVE_SUPPORT_STATES:
                if support.zone_low is not None and support.zone_high is not None:
                    if entry < support.zone_low:
                        levels.append(support.zone_low)
                    elif entry > support.zone_high:
                        levels.append(support.zone_high)
                    else:
                        levels.append(entry)
                levels.extend(item.price for item in support.structural_supports)
        return min(levels, key=lambda level: abs(entry - level)) if levels else None


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}


def _positive_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None
