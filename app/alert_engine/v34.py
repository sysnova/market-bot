"""Volume-structure alerts and bounded confluence enrichment."""

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
    NamedValue,
    PatternDirection,
)

from .v33 import AlertEngineV33

ZERO = Decimal()
HUNDRED = Decimal("100")
BOOSTED_KINDS = {
    AlertKind.SWING_SETUP,
    AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION,
    AlertKind.ENTRY_CONFIRMED,
    AlertKind.HIGH_CONVICTION_BUY,
}


class AlertEngineV34(AlertEngineV33):
    """Surface weekly OBV evidence without relaxing entry confirmation gates."""

    engine_version = "3.4.0"

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
        if result.horizon is not AnalysisHorizon.VOLUME_STRUCTURE:
            return super().ingest(result, now=now)
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("now must be timezone-aware UTC")
        if result.as_of > now:
            raise ValueError("analysis as_of cannot be in the future")
        current = self._latest.setdefault(result.symbol, {}).get(result.horizon)
        if current is not None and (
            current.analysis_id == result.analysis_id or result.as_of < current.as_of
        ):
            return None
        self._latest[result.symbol][result.horizon] = result
        state = _metric(result, "divergence_state")
        if state not in {
            "DEVELOPING",
            "DIVERGENCE_CONFIRMED",
            "RECLAIM_CONFIRMED",
        }:
            return None
        deduplication_key = (
            f"alert:v3.4:{result.symbol.lower()}:obv-divergence:"
            f"{str(state).lower()}:{result.context_hash}"
        )
        if deduplication_key in self._emitted_keys:
            return None
        title_state = str(state).replace("_", " ")
        alert = LocalAlert(
            symbol=result.symbol,
            created_at=now,
            kind=AlertKind.OBV_BULLISH_DIVERGENCE,
            severity=AlertSeverity.WATCH,
            title=f"{result.symbol} WEEKLY OBV {title_state}",
            message=(
                "Weekly price/OBV structure suggests possible accumulation or "
                "selling-pressure absorption; price confirmation remains independent"
            ),
            horizons=(AnalysisHorizon.VOLUME_STRUCTURE,),
            component_analysis_ids=(result.analysis_id,),
            component_analyses=(result,),
            metrics=result.metrics,
            score=result.score,
            reasons=result.reasons,
            deduplication_key=deduplication_key,
            expires_at=now + timedelta(days=14),
        )
        self._emitted_keys.add(deduplication_key)
        return alert

    def _build_named_alert(
        self,
        symbol: str,
        kind: AlertKind,
        components: tuple[AnalysisResult, ...],
        fresh: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> LocalAlert | None:
        alert = super()._build_named_alert(symbol, kind, components, fresh, now)
        if alert is None or kind not in BOOSTED_KINDS:
            return alert
        volume = fresh.get(AnalysisHorizon.VOLUME_STRUCTURE)
        boost = volume_structure_boost(volume)
        if volume is None or boost <= ZERO:
            return alert
        effective = min(HUNDRED, alert.score + boost).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        embedded = tuple(dict.fromkeys((*alert.component_analyses, volume)))
        return alert.model_copy(
            update={
                "horizons": tuple(dict.fromkeys((*alert.horizons, volume.horizon))),
                "component_analysis_ids": tuple(item.analysis_id for item in embedded),
                "component_analyses": embedded,
                "metrics": (
                    *alert.metrics,
                    NamedValue(name="base_score", value=alert.score),
                    NamedValue(name="volume_structure_boost", value=boost),
                    NamedValue(name="effective_score", value=effective),
                ),
                "score": effective,
                "reasons": tuple(
                    dict.fromkeys(
                        (*alert.reasons, f"volume_structure_boost:+{boost}")
                    )
                ),
            }
        )


def volume_structure_boost(result: AnalysisResult | None) -> Decimal:
    if (
        result is None
        or result.horizon is not AnalysisHorizon.VOLUME_STRUCTURE
        or result.direction is not PatternDirection.BULLISH
        or result.verdict not in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}
    ):
        return ZERO
    value = _metric(result, "evidence_boost")
    if isinstance(value, bool):
        return ZERO
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return ZERO
    return min(Decimal("10"), max(ZERO, parsed))


def _metric(result: AnalysisResult, name: str) -> object | None:
    return next((item.value for item in result.metrics if item.name == name), None)
