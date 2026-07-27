"""Entry Watcher v2 confirmation policy."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    PatternDirection,
)

from .engine import EntryWatcher
from .models import EntryWatch

_SWING_CONFIRMATIONS = {"breakout", "pullback"}
_INTRADAY_CONFIRMATIONS = {
    "bullish_breakout",
    "bullish_vwap_reclaim",
    "bullish_entry_confirmation",
}


class EntryWatcherV2(EntryWatcher):
    """Confirm only named bullish triggers and ignore isolated intraday wicks."""

    engine_version = "2.0.0"

    def _confirmed(
        self, analyses: dict[AnalysisHorizon, AnalysisResult], *, now: datetime
    ) -> bool:
        required = {
            AnalysisHorizon.LONG_TERM,
            AnalysisHorizon.SWING,
            AnalysisHorizon.INTRADAY,
        }
        if not required.issubset(analyses):
            return False
        if any(
            now - analyses[horizon].as_of > self._policy.max_ages[horizon]
            for horizon in required
        ):
            return False
        long_term = analyses[AnalysisHorizon.LONG_TERM]
        swing = analyses[AnalysisHorizon.SWING]
        intraday = analyses[AnalysisHorizon.INTRADAY]
        swing_metrics = _result_metrics(swing)
        intraday_metrics = _result_metrics(intraday)
        quality = intraday_metrics.get("confirmation_quality")
        quality_ok = quality is None or quality in {"standard", "strong"}
        return (
            long_term.direction is PatternDirection.BULLISH
            and long_term.verdict
            not in {AnalysisVerdict.AVOID, AnalysisVerdict.INSUFFICIENT_DATA}
            and swing.direction is PatternDirection.BULLISH
            and swing.verdict is AnalysisVerdict.FAVORABLE
            and swing_metrics.get("classification") in _SWING_CONFIRMATIONS
            and intraday.direction is PatternDirection.BULLISH
            and intraday.verdict is AnalysisVerdict.FAVORABLE
            and intraday_metrics.get("setup") in _INTRADAY_CONFIRMATIONS
            and quality_ok
        )

    @staticmethod
    def _invalidation_reason(
        watch: EntryWatch, *, result: AnalysisResult, current_price: Decimal
    ) -> str | None:
        if result.horizon is AnalysisHorizon.LONG_TERM and (
            result.verdict is AnalysisVerdict.AVOID
            or result.direction is PatternDirection.BEARISH
        ):
            return "long_structure_invalidated"
        if (
            result.horizon is AnalysisHorizon.LONG_TERM
            and (long_close := _decimal_metric(result, "reference_price")) is not None
            and long_close <= watch.invalidation
        ):
            return "original_invalidation_breached_on_long_close"
        return None

    def _confirmation_reasons(
        self, analyses: dict[AnalysisHorizon, AnalysisResult]
    ) -> tuple[str, ...]:
        return (
            "regime_aware_entry_confirmed",
            self._dilution_warning(analyses),
        )


EntryWatcherV1 = EntryWatcher


def _result_metrics(result: AnalysisResult) -> dict[str, Any]:
    return {item.name: item.value for item in result.metrics}


def _decimal_metric(result: AnalysisResult, name: str) -> Decimal | None:
    value = _result_metrics(result).get(name)
    return value if isinstance(value, Decimal) else None
