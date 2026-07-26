"""Stateful latest-value aggregation with explicit time inputs."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryWatchStatus,
    EntryWatchTransition,
    LocalAlert,
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
        return self._aggregate(result.symbol, now)

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
            EntryWatchStatus.TRIGGERED: (AlertSeverity.ACTION, Decimal("85")),
            EntryWatchStatus.INVALIDATED: (AlertSeverity.INFO, Decimal("20")),
            EntryWatchStatus.EXPIRED: (AlertSeverity.INFO, Decimal("10")),
        }[transition.status]
        status = transition.status.value
        return LocalAlert(
            symbol=transition.symbol,
            created_at=now,
            severity=severity,
            title=f"{transition.symbol} ENTRY {status}",
            message=(
                f"price {transition.current_price}; original zone "
                f"{transition.zone_low}-{transition.zone_high}; "
                f"invalidation {transition.invalidation}"
            ),
            horizons=transition.horizons,
            component_analysis_ids=transition.source_analysis_ids,
            score=score,
            reasons=transition.reasons,
            deduplication_key=(
                f"entry-watch:v1:{transition.watch_id}:"
                f"{transition.transition_id}:{status.lower()}"
            ),
            expires_at=now + self._policy.alert_ttl,
        )

    def _aggregate(self, symbol: str, now: datetime) -> LocalAlert | None:
        fresh = self._fresh_values(symbol, now)
        if len(fresh) < self._policy.min_fresh_horizons:
            return None
        if any(horizon not in fresh for horizon in self._policy.required_horizons):
            return None
        dilution = fresh[AnalysisHorizon.DILUTION]
        if dilution.verdict is AnalysisVerdict.AVOID:
            return self._build_dilution_veto(symbol, fresh, dilution, now)

        direction, raw_score = self._directional_score(fresh)
        if direction is None:
            return None
        score, dilution_reason = self._apply_dilution(raw_score, fresh, dilution)
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
        fresh: dict[AnalysisHorizon, AnalysisResult],
        dilution: AnalysisResult,
    ) -> tuple[Decimal, str | None]:
        directional_weight = sum(
            (
                self._policy.for_horizon(horizon).weight
                for horizon in (
                    AnalysisHorizon.LONG_TERM,
                    AnalysisHorizon.SWING,
                    AnalysisHorizon.INTRADAY,
                )
                if horizon in fresh
            ),
            ZERO,
        )
        ratio = self._policy.for_horizon(AnalysisHorizon.DILUTION).weight / directional_weight
        penalty = dilution.score * ratio
        reason: str | None = None
        if dilution.verdict is AnalysisVerdict.CAUTION:
            penalty += self._policy.dilution_caution_penalty
            reason = "dilution_caution_penalty"
        elif dilution.verdict is AnalysisVerdict.WATCH:
            reason = "dilution_watch_penalty"
        elif dilution.verdict is AnalysisVerdict.FAVORABLE:
            penalty *= Decimal("0.25")
        return _score(raw_score - penalty), reason

    def _build_dilution_veto(
        self,
        symbol: str,
        fresh: dict[AnalysisHorizon, AnalysisResult],
        dilution: AnalysisResult,
        now: datetime,
    ) -> LocalAlert | None:
        score = max(Decimal("90"), dilution.score)
        reasons = ("dilution_avoid_veto", *self._component_reasons(fresh))
        return self._build_alert(
            symbol,
            fresh,
            "DILUTION VETO",
            AlertSeverity.CRITICAL,
            score,
            reasons,
            now,
        )

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


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
