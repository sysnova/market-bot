"""Fresh material news risk as a fail-closed gate for new entry signals."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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

from .v35 import ACTIONABLE_KINDS, AlertEngineV35


class AlertEngineV36(AlertEngineV35):
    """News can warn or gate; it can never create a positive entry alert."""

    engine_version = "3.6.0"

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
        if result.horizon is not AnalysisHorizon.NEWS:
            return super().ingest(result, now=now)
        _require_utc(now)
        if result.as_of > now:
            raise ValueError("analysis as_of cannot be in the future")
        symbol_values = self._latest.setdefault(result.symbol, {})
        existing = symbol_values.get(AnalysisHorizon.NEWS)
        if existing is not None and (
            result.analysis_id == existing.analysis_id or result.as_of < existing.as_of
        ):
            return None
        symbol_values[AnalysisHorizon.NEWS] = result
        if not self._blocks(result, now=now):
            return None
        deduplication_key = f"news-risk:v1:{result.symbol.lower()}:{result.context_hash}"
        if deduplication_key in self._emitted_keys:
            return None
        expires_at = news_expiry(result) or now + self._policy.alert_ttl
        alert = LocalAlert(
            symbol=result.symbol,
            created_at=now,
            kind=AlertKind.NEWS_RISK,
            severity=AlertSeverity.CRITICAL,
            title=f"{result.symbol} NEWS RISK",
            message=(
                "material bearish news blocks new entry signals until expiry; "
                "informational risk control only, no sell order was submitted"
            ),
            horizons=(AnalysisHorizon.NEWS,),
            component_analysis_ids=(result.analysis_id,),
            component_analyses=(result,),
            metrics=(NamedValue(name="news_expires_at", value=expires_at),),
            score=result.score,
            reasons=("material_bearish_news_entry_gate", *result.reasons),
            deduplication_key=deduplication_key,
            expires_at=expires_at,
        )
        self._emitted_keys.add(deduplication_key)
        return alert

    def news_blocks_entry(self, symbol: str, *, now: datetime) -> bool:
        result = self._latest.get(symbol.strip().upper(), {}).get(AnalysisHorizon.NEWS)
        return result is not None and self._blocks(result, now=now)

    def _build_named_alert(
        self,
        symbol: str,
        kind: AlertKind,
        components: tuple[AnalysisResult, ...],
        fresh: dict[AnalysisHorizon, AnalysisResult],
        now: datetime,
    ) -> LocalAlert | None:
        if kind in ACTIONABLE_KINDS and self.news_blocks_entry(symbol, now=now):
            return None
        return super()._build_named_alert(symbol, kind, components, fresh, now)

    @staticmethod
    def _blocks(result: AnalysisResult, *, now: datetime) -> bool:
        expires_at = news_expiry(result)
        materiality = news_metric(result, "materiality")
        return (
            result.direction is PatternDirection.BEARISH
            and result.verdict is AnalysisVerdict.AVOID
            and result.confidence >= Decimal("0.65")
            and materiality in {"HIGH", "CRITICAL"}
            and expires_at is not None
            and expires_at > now
        )


def news_metric(result: AnalysisResult, name: str) -> object | None:
    return next((item.value for item in result.metrics if item.name == name), None)


def news_expiry(result: AnalysisResult) -> datetime | None:
    value = news_metric(result, "expires_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    return None


def _require_utc(value: datetime) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise ValueError("now must be timezone-aware UTC")
