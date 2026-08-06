"""Explicit multi-horizon confluence alerts for the distributed v2 runtime."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    PatternDirection,
)

from .engine import AlertEngine
from .policy import AlertPolicy

ZERO = Decimal("0")
HUNDRED = Decimal("100")
_SEVERITY_RANK = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WATCH: 1,
    AlertSeverity.ACTION: 2,
    AlertSeverity.CRITICAL: 3,
}


class AlertEngineV2(AlertEngine):
    """Emit named setup and confirmation alerts from the latest engine results."""

    engine_id = "alert"
    engine_version = "2.0.0"

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
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
        if result.horizon is AnalysisHorizon.DILUTION:
            return self._dilution_warning_alert(result, now=now)

        fresh = self._fresh_values(result.symbol, now)
        selected = self._select_alert(result, fresh)
        if selected is None:
            return None
        kind, components = selected
        return self._build_named_alert(result.symbol, kind, components, fresh, now)

    def _select_alert(
        self,
        incoming: AnalysisResult,
        fresh: dict[AnalysisHorizon, AnalysisResult],
    ) -> tuple[AlertKind, tuple[AnalysisResult, ...]] | None:
        long = _bullish(fresh.get(AnalysisHorizon.LONG_TERM), self._policy.watch_threshold)
        swing = _bullish(fresh.get(AnalysisHorizon.SWING), self._policy.watch_threshold)
        intraday = _bullish(fresh.get(AnalysisHorizon.INTRADAY), self._policy.watch_threshold)
        if long is not None and swing is not None and intraday is not None:
            return AlertKind.HIGH_CONVICTION_BUY, (long, swing, intraday)
        if intraday is not None and (swing is not None or long is not None):
            directional = tuple(item for item in (long, swing, intraday) if item is not None)
            return AlertKind.ENTRY_CONFIRMED, directional
        bearish = tuple(
            item
            for horizon in (
                AnalysisHorizon.LONG_TERM,
                AnalysisHorizon.SWING,
                AnalysisHorizon.INTRADAY,
            )
            if (item := fresh.get(horizon)) is not None and _is_bearish(item)
        )
        if len(bearish) == 3:
            return AlertKind.BEARISH_CONSENSUS, bearish
        if incoming.horizon is AnalysisHorizon.LONG_TERM and long is not None:
            return AlertKind.LONG_BUY_ZONE, (long,)
        if incoming.horizon is AnalysisHorizon.SWING and swing is not None:
            return AlertKind.SWING_SETUP, (swing,)
        return None

    def _build_named_alert(
        self,
        symbol: str,
        kind: AlertKind,
        components: tuple[AnalysisResult, ...],
        fresh: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> LocalAlert | None:
        score = _combined_score(components, self._policy)
        severity = _severity(kind, score, self._policy.critical_threshold)
        component_key = "-".join(item.horizon.value.lower() for item in components)
        state_key = (symbol, f"{kind.value}:{component_key}")
        previous = self._last_emitted.get(state_key)
        if previous is not None:
            previous_at, previous_severity = previous
            if (
                now - previous_at < self._policy.cooldown
                and _SEVERITY_RANK[severity] <= _SEVERITY_RANK[previous_severity]
            ):
                return None
        window = int(now.timestamp()) // int(self._policy.cooldown.total_seconds())
        deduplication_key = (
            f"alert:v2:{symbol.lower()}:{kind.value.lower()}:{component_key}:"
            f"{severity.value.lower()}:{window}"
        )
        if deduplication_key in self._emitted_keys:
            return None
        dilution = fresh.get(AnalysisHorizon.DILUTION)
        reasons = [kind.value.lower()]
        reasons.extend(
            f"{item.horizon.value.lower()}:{item.verdict.value.lower()}:{item.score}"
            for item in components
        )
        if dilution is None:
            reasons.append("dilution_analysis_unavailable")
        elif dilution.verdict is not AnalysisVerdict.FAVORABLE:
            reasons.append(f"dilution_{dilution.verdict.value.lower()}_warning")
        embedded = (*components, *((dilution,) if dilution is not None else ()))
        alert = LocalAlert(
            symbol=symbol,
            created_at=now,
            kind=kind,
            severity=severity,
            title=f"{symbol} {kind.value.replace('_', ' ')}",
            message=_message(kind, len(components), score),
            horizons=tuple(item.horizon for item in embedded),
            component_analysis_ids=tuple(item.analysis_id for item in embedded),
            component_analyses=embedded,
            score=score,
            reasons=_unique(tuple(reasons)),
            deduplication_key=deduplication_key,
            expires_at=now + self._policy.alert_ttl,
        )
        self._emitted_keys.add(deduplication_key)
        self._last_emitted[state_key] = (now, severity)
        return alert


def _bullish(result: AnalysisResult | None, minimum: Decimal) -> AnalysisResult | None:
    if result is None:
        return None
    if result.direction is not PatternDirection.BULLISH:
        return None
    if result.verdict not in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}:
        return None
    return result if result.score * result.confidence >= minimum else None


def _is_bearish(result: AnalysisResult) -> bool:
    return result.direction is PatternDirection.BEARISH and result.verdict in {
        AnalysisVerdict.CAUTION,
        AnalysisVerdict.AVOID,
    }


def _combined_score(
    components: tuple[AnalysisResult, ...], policy: AlertPolicy
) -> Decimal:
    weighted = ZERO
    total = ZERO
    for item in components:
        horizon_policy = policy.for_horizon(item.horizon)
        weighted += horizon_policy.weight * item.score * item.confidence
        total += horizon_policy.weight
    return _score(weighted / total if total else ZERO)


def _severity(
    kind: AlertKind,
    score: Decimal,
    critical_threshold: Decimal,
) -> AlertSeverity:
    if kind in {AlertKind.LONG_BUY_ZONE, AlertKind.SWING_SETUP}:
        return AlertSeverity.WATCH
    if kind is AlertKind.BEARISH_CONSENSUS:
        return AlertSeverity.CRITICAL if score >= critical_threshold else AlertSeverity.ACTION
    if kind is AlertKind.HIGH_CONVICTION_BUY and score >= critical_threshold:
        return AlertSeverity.CRITICAL
    return AlertSeverity.ACTION


def _message(kind: AlertKind, count: int, score: Decimal) -> str:
    if kind is AlertKind.LONG_BUY_ZONE:
        return f"Long engine identified an attractive buy zone; score {score}"
    if kind is AlertKind.SWING_SETUP:
        return f"Swing engine identified an intact actionable structure; score {score}"
    if kind is AlertKind.ENTRY_CONFIRMED:
        return f"Intraday timing confirms a setup across {count} aligned engines; score {score}"
    if kind is AlertKind.HIGH_CONVICTION_BUY:
        return f"Long, Swing, and Intraday are aligned; score {score}"
    return f"Bearish alignment across {count} engines; score {score}"


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("now must be timezone-aware UTC")


def _score(value: Decimal) -> Decimal:
    return min(HUNDRED, max(ZERO, value)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
