"""Stateful latest-value aggregation with explicit time inputs."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryCloseReason,
    EntryOpportunityEvent,
    EntryOpportunityStatus,
    EntrySignalFamily,
    EntryWatchStatus,
    EntryWatchTransition,
    LocalAlert,
    NamedValue,
    PatternDirection,
)

from .policy import AlertPolicy

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_SEVERITY_RANK = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WATCH: 1,
    AlertSeverity.ACTION: 2,
    AlertSeverity.CRITICAL: 3,
}


class AlertEngine:
    """Keep latest analyses and emit cooldown-aware human notifications."""

    def __init__(self, policy: AlertPolicy | None = None) -> None:
        self._policy = policy or AlertPolicy()
        self._latest: dict[str, dict[AnalysisHorizon, AnalysisResult]] = {}
        self._last_emitted: dict[tuple[str, str], tuple[datetime, AlertSeverity]] = {}
        self._emitted_keys: set[str] = set()

    def latest(self, symbol: str) -> dict[AnalysisHorizon, AnalysisResult]:
        """Return a defensive copy of the latest values for one symbol."""

        return dict(self._latest.get(symbol.strip().upper(), {}))

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
        """Store a result and aggregate fresh values for its symbol."""

        _require_utc(now)
        if result.as_of > now:
            raise ValueError("analysis as_of cannot be in the future")
        symbol_values = self._latest.setdefault(result.symbol, {})
        existing = symbol_values.get(result.horizon)
        if existing is not None and (
            result.analysis_id == existing.analysis_id or result.as_of < existing.as_of
        ):
            return None
        symbol_values[result.horizon] = result
        aggregate = self._aggregate(result.symbol, now)
        if aggregate is not None:
            return aggregate
        if result.horizon is AnalysisHorizon.DILUTION:
            return self._dilution_warning_alert(result, now=now)
        return None

    def ingest_entry_watch(
        self, transition: EntryWatchTransition, *, now: datetime
    ) -> LocalAlert:
        """Render one durable entry-watch transition as a human-only alert."""
        _require_utc(now)
        if transition.occurred_at > now:
            raise ValueError("entry watch transition cannot be in the future")
        severity, score = {
            EntryWatchStatus.ARMED: (AlertSeverity.INFO, Decimal("40")),
            EntryWatchStatus.IN_ZONE: (AlertSeverity.WATCH, Decimal("65")),
            EntryWatchStatus.EARLY_ENTRY: (AlertSeverity.ACTION, Decimal("72")),
            EntryWatchStatus.IMPULSE_EXTENDED: (AlertSeverity.WATCH, Decimal("55")),
            EntryWatchStatus.TRIGGERED: (AlertSeverity.ACTION, Decimal("85")),
            EntryWatchStatus.POLICY_INELIGIBLE: (AlertSeverity.INFO, Decimal("15")),
            EntryWatchStatus.INVALIDATED: (AlertSeverity.INFO, Decimal("20")),
            EntryWatchStatus.EXPIRED: (AlertSeverity.INFO, Decimal("10")),
        }[transition.status]
        status = transition.status.value
        breakaway_pending = "breakaway_continuation_pending" in transition.reasons
        chase_cap_exceeded = "continuation_chase_cap_exceeded" in transition.reasons
        if breakaway_pending:
            severity, score = AlertSeverity.WATCH, Decimal("75")
            title = f"{transition.symbol} ENTRY BREAKAWAY WATCH"
            decision = (
                "recent zone touch remains valid; moderate continuation is awaiting "
                "fresh intraday confirmation"
            )
        elif transition.status is EntryWatchStatus.EARLY_ENTRY:
            title = f"{transition.symbol} ENTRY EARLY L1"
            decision = "partial early entry confirmed inside the efficient window"
        elif transition.status is EntryWatchStatus.IMPULSE_EXTENDED:
            title = f"{transition.symbol} ENTRY AWAITING PULLBACK"
            decision = "initial entry window was missed; tracking a dynamic impulse pullback"
        elif transition.status is EntryWatchStatus.IN_ZONE:
            title = f"{transition.symbol} ENTRY IN_ZONE EARLY WATCH"
            decision = "early entry watch; price reached the frozen thesis zone"
        elif chase_cap_exceeded:
            title = f"{transition.symbol} ENTRY EXTENDED WAIT"
            decision = "continuation exceeded the chase cap; wait for a retest"
        else:
            title = f"{transition.symbol} ENTRY {status}"
            decision = "entry-watch thesis updated"
        fresh = self._fresh_values(transition.symbol, now)
        tactical_levels = ()
        tactical_message = ""
        if transition.entry_invalidation is not None:
            tactical_levels += (
                NamedValue(
                    name="entry_invalidation",
                    value=transition.entry_invalidation,
                ),
            )
            tactical_message += f"; tactical stop {transition.entry_invalidation}"
        if transition.entry_target is not None:
            tactical_levels += (
                NamedValue(name="entry_target", value=transition.entry_target),
            )
            tactical_message += f"; tactical target {transition.entry_target}"
        return LocalAlert(
            symbol=transition.symbol,
            created_at=now,
            kind=AlertKind.ENTRY_WATCH,
            severity=severity,
            title=title,
            message=(
                f"{decision}; price {transition.current_price}; original zone "
                f"{transition.zone_low}-{transition.zone_high}; "
                f"structural invalidation {transition.invalidation}{tactical_message}"
            ),
            horizons=transition.horizons,
            component_analysis_ids=transition.source_analysis_ids,
            component_analyses=self._ordered_analyses(fresh),
            metrics=(
                NamedValue(name="current_price", value=transition.current_price),
                NamedValue(name="buy_zone_low", value=transition.zone_low),
                NamedValue(name="buy_zone_high", value=transition.zone_high),
                NamedValue(name="invalidation", value=transition.invalidation),
                NamedValue(name="watch_expires_at", value=transition.watch_expires_at),
                *tactical_levels,
            ),
            score=score,
            reasons=transition.reasons,
            deduplication_key=(
                f"entry-watch:v1:{transition.watch_id}:"
                f"{transition.transition_id}:{status.lower()}"
            ),
            expires_at=now + self._policy.alert_ttl,
        )

    def ingest_entry_opportunity(
        self, event: EntryOpportunityEvent, *, now: datetime
    ) -> LocalAlert:
        """Render durable lifecycle progress and closures for the focused monitor."""

        _require_utc(now)
        if event.occurred_at > now:
            raise ValueError("entry opportunity event cannot be in the future")
        opportunity = event.opportunity
        analytical_family = opportunity.primary_signal_family not in {
            EntrySignalFamily.CORE_ENTRY,
            EntrySignalFamily.CORE_RECOVERY,
        }
        closed = opportunity.status is EntryOpportunityStatus.CLOSED
        if closed:
            severity = (
                AlertSeverity.CRITICAL
                if opportunity.close_reason is EntryCloseReason.ORIGINAL_THESIS_INVALIDATED
                else AlertSeverity.ACTION
            )
            kind = AlertKind.ENTRY_OPPORTUNITY_CLOSED
            reason = opportunity.close_reason.value.replace("_", " ")  # type: ignore[union-attr]
            title = f"{opportunity.symbol} ENTRY CLOSED - {reason}"
            message = (
                f"paper opportunity closed at {opportunity.current_price}; "
                f"peak maturity {opportunity.peak_maturity.value}"
            )
        elif analytical_family:
            severity = AlertSeverity.ACTION
            kind = AlertKind.ENTRY_OPPORTUNITY_PROGRESS
            family = opportunity.primary_signal_family.value.replace("_", " ")
            title = f"{opportunity.symbol} {family} PAPER TRACK"
            message = (
                f"analytical family {family}; paper price {opportunity.current_price}; "
                f"progress {opportunity.progress_percent}%"
            )
        else:
            severity = {
                EntryOpportunityStatus.ARMED: AlertSeverity.INFO,
                EntryOpportunityStatus.IN_ZONE: AlertSeverity.WATCH,
                EntryOpportunityStatus.CONFIRMING: AlertSeverity.ACTION,
                EntryOpportunityStatus.OPEN: AlertSeverity.CRITICAL,
            }[opportunity.status]
            kind = AlertKind.ENTRY_OPPORTUNITY_PROGRESS
            title = (
                f"{opportunity.symbol} ENTRY {opportunity.current_maturity.value} "
                f"{_progress_bar(opportunity.progress_percent)}"
            )
            message = (
                f"maturity {opportunity.current_maturity.value}; "
                f"progress {opportunity.progress_percent}%; price {opportunity.current_price}"
            )
        horizons = tuple(dict.fromkeys(item.horizon for item in opportunity.legs))
        if not horizons:
            horizons = (AnalysisHorizon.LONG_TERM,)
        return LocalAlert(
            symbol=opportunity.symbol,
            created_at=now,
            kind=kind,
            severity=severity,
            title=title,
            message=message,
            horizons=horizons,
            component_analysis_ids=opportunity.source_analysis_ids,
            component_analyses=opportunity.latest_analyses,
            metrics=(
                NamedValue(name="current_price", value=opportunity.current_price),
                NamedValue(name="buy_zone_low", value=opportunity.zone_low),
                NamedValue(name="buy_zone_high", value=opportunity.zone_high),
                NamedValue(name="invalidation", value=opportunity.invalidation),
                NamedValue(name="progress_percent", value=opportunity.progress_percent),
                NamedValue(name="maturity", value=opportunity.current_maturity.value),
                NamedValue(
                    name="signal_family",
                    value=opportunity.primary_signal_family.value,
                ),
                NamedValue(
                    name="open_horizon_count",
                    value=sum(item.status.value == "OPEN" for item in opportunity.legs),
                ),
            ),
            score=opportunity.progress_percent,
            reasons=event.reasons,
            deduplication_key=f"entry-opportunity:v1:{event.event_id}",
            expires_at=None if closed else now + self._policy.alert_ttl,
        )

    def _aggregate(self, symbol: str, now: datetime) -> LocalAlert | None:
        fresh = self._fresh_values(symbol, now)
        if len(fresh) < self._policy.min_fresh_horizons:
            return None
        if any(horizon not in fresh for horizon in self._policy.required_horizons):
            return None
        direction, raw_score = self._directional_score(fresh)
        if direction is None:
            return None
        score, dilution_reason = self._apply_dilution(
            raw_score, fresh.get(AnalysisHorizon.DILUTION)
        )
        severity = self._severity(score)
        if severity is None:
            return None
        reasons = [f"{direction.lower()}_consensus"]
        if dilution_reason is not None:
            reasons.append(dilution_reason)
        reasons.extend(self._component_reasons(fresh))
        return self._build_alert(symbol, fresh, direction, severity, score, tuple(reasons), now)

    def _fresh_values(
        self, symbol: str, now: datetime
    ) -> dict[AnalysisHorizon, AnalysisResult]:
        values = self._latest.get(symbol, {})
        return {
            horizon: result
            for horizon, result in values.items()
            if result.verdict is not AnalysisVerdict.INSUFFICIENT_DATA
            and now - result.as_of <= self._policy.for_horizon(horizon).max_age
        }

    def _directional_score(
        self, fresh: dict[AnalysisHorizon, AnalysisResult]
    ) -> tuple[str | None, Decimal]:
        weighted = ZERO
        total_weight = ZERO
        for horizon in (
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        ):
            result = fresh.get(horizon)
            if result is None:
                continue
            weight = self._policy.for_horizon(horizon).weight
            strength = self._verdict_strength(result) * result.confidence
            sign = {
                PatternDirection.BULLISH: Decimal("1"),
                PatternDirection.BEARISH: Decimal("-1"),
                PatternDirection.NEUTRAL: ZERO,
            }[result.direction]
            weighted += weight * strength * sign
            total_weight += weight
        if total_weight == ZERO:
            return None, ZERO
        net = weighted / total_weight
        if net == ZERO:
            return None, ZERO
        direction = "BULLISH" if net > ZERO else "BEARISH"
        return direction, _score(abs(net))

    @staticmethod
    def _verdict_strength(result: AnalysisResult) -> Decimal:
        if result.verdict is AnalysisVerdict.FAVORABLE:
            return result.score
        if result.verdict is AnalysisVerdict.WATCH:
            return result.score * Decimal("0.8")
        if result.verdict is AnalysisVerdict.CAUTION:
            return max(result.score * Decimal("0.5"), Decimal("55"))
        if result.verdict is AnalysisVerdict.AVOID:
            return max(result.score, Decimal("90"))
        return ZERO

    def _apply_dilution(
        self,
        raw_score: Decimal,
        dilution: AnalysisResult | None,
    ) -> tuple[Decimal, str | None]:
        if dilution is None:
            return raw_score, "dilution_analysis_unavailable"
        reason = {
            AnalysisVerdict.FAVORABLE: None,
            AnalysisVerdict.WATCH: "dilution_watch_warning",
            AnalysisVerdict.CAUTION: "dilution_caution_warning",
            AnalysisVerdict.AVOID: "dilution_avoid_warning",
            AnalysisVerdict.INSUFFICIENT_DATA: "dilution_analysis_unavailable",
        }[dilution.verdict]
        return raw_score, reason

    def _dilution_warning_alert(
        self, result: AnalysisResult, *, now: datetime
    ) -> LocalAlert | None:
        warning = {
            AnalysisVerdict.FAVORABLE: None,
            AnalysisVerdict.WATCH: "dilution_watch_warning",
            AnalysisVerdict.CAUTION: "dilution_caution_warning",
            AnalysisVerdict.AVOID: "dilution_avoid_warning",
            AnalysisVerdict.INSUFFICIENT_DATA: None,
        }[result.verdict]
        if warning is None:
            return None
        deduplication_key = (
            f"sec-warning:v1:{result.symbol.lower()}:"
            f"{result.verdict.value.lower()}:{result.context_hash}"
        )
        if deduplication_key in self._emitted_keys:
            return None
        alert = LocalAlert(
            symbol=result.symbol,
            created_at=now,
            kind=AlertKind.SEC_WARNING,
            severity=AlertSeverity.WATCH,
            title=f"{result.symbol} SEC DILUTION WARNING",
            message="informational SEC risk only; does not gate entries or submit orders",
            horizons=(AnalysisHorizon.DILUTION,),
            component_analysis_ids=(result.analysis_id,),
            component_analyses=(result,),
            score=result.score,
            reasons=_unique((warning, *result.reasons)),
            deduplication_key=deduplication_key,
            expires_at=now + self._policy.alert_ttl,
        )
        self._emitted_keys.add(deduplication_key)
        return alert

    def _build_alert(
        self,
        symbol: str,
        fresh: dict[AnalysisHorizon, AnalysisResult],
        direction: str,
        severity: AlertSeverity,
        score: Decimal,
        reasons: tuple[str, ...],
        now: datetime,
    ) -> LocalAlert | None:
        state_key = (symbol, direction)
        previous = self._last_emitted.get(state_key)
        if previous is not None:
            previous_at, previous_severity = previous
            within_cooldown = now - previous_at < self._policy.cooldown
            not_escalated = _SEVERITY_RANK[severity] <= _SEVERITY_RANK[previous_severity]
            if within_cooldown and not_escalated:
                return None
        window_seconds = int(self._policy.cooldown.total_seconds())
        window = int(now.timestamp()) // window_seconds
        deduplication_key = (
            f"alert:v1:{symbol.lower()}:{direction.lower().replace(' ', '-')}:"
            f"{severity.value.lower()}:{window}"
        )
        if deduplication_key in self._emitted_keys:
            return None
        ordered = tuple(horizon for horizon in AnalysisHorizon if horizon in fresh)
        components = tuple(fresh[horizon].analysis_id for horizon in ordered)
        alert = LocalAlert(
            symbol=symbol,
            created_at=now,
            severity=severity,
            title=f"{symbol} {direction} {severity.value}",
            message=f"weighted score {score}; inspect {len(components)} component analyses",
            horizons=ordered,
            component_analysis_ids=components,
            component_analyses=self._ordered_analyses(fresh),
            score=score,
            reasons=_unique(reasons),
            deduplication_key=deduplication_key,
            expires_at=now + self._policy.alert_ttl,
        )
        self._emitted_keys.add(deduplication_key)
        self._last_emitted[state_key] = (now, severity)
        return alert

    def _severity(self, score: Decimal) -> AlertSeverity | None:
        if score >= self._policy.critical_threshold:
            return AlertSeverity.CRITICAL
        if score >= self._policy.action_threshold:
            return AlertSeverity.ACTION
        if score >= self._policy.watch_threshold:
            return AlertSeverity.WATCH
        return None

    @staticmethod
    def _component_reasons(
        fresh: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[str, ...]:
        return tuple(
            f"{horizon.value.lower()}:{result.verdict.value.lower()}:{result.score}"
            for horizon, result in sorted(fresh.items(), key=lambda item: item[0].value)
        )

    @staticmethod
    def _ordered_analyses(
        values: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[AnalysisResult, ...]:
        order = (
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
            AnalysisHorizon.DILUTION,
        )
        return tuple(values[horizon] for horizon in order if horizon in values)


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")


def _progress_bar(percent: Decimal) -> str:
    filled = min(10, max(0, int(percent / Decimal("10"))))
    return f"[{'#' * filled}{'-' * (10 - filled)}]"


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
