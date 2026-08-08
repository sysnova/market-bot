"""Swing continuation confirmations that do not require a Long thesis."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    PatternDirection,
)

from .policy import AlertPolicy
from .v2 import AlertEngineV2

_NEW_YORK = ZoneInfo("America/New_York")
_CONTINUATION_SETUPS = {
    "bullish_breakout",
    "bullish_vwap_reclaim",
    "bullish_entry_confirmation",
}


class AlertEngineV3(AlertEngineV2):
    """Confirm L2 after two fresh strong Intraday higher lows over a valid Swing setup."""

    engine_id = "alert"
    engine_version = "3.0.0"

    def __init__(
        self,
        policy: AlertPolicy | None = None,
        *,
        minimum_reconfirmation_delay: timedelta = timedelta(minutes=3),
        strong_confirmation_required: bool = True,
        five_minute_higher_low_required: bool = True,
        same_market_session_required: bool = True,
    ) -> None:
        super().__init__(policy)
        if minimum_reconfirmation_delay <= timedelta():
            raise ValueError("minimum reconfirmation delay must be positive")
        self._minimum_reconfirmation_delay = minimum_reconfirmation_delay
        self._strong_confirmation_required = strong_confirmation_required
        self._five_minute_higher_low_required = five_minute_higher_low_required
        self._same_market_session_required = same_market_session_required
        self._swing_continuation_candidates: dict[str, tuple[UUID, datetime, date]] = {}
        self._swing_continuation_sessions: set[tuple[str, date]] = set()

    def ingest(self, result: AnalysisResult, *, now: datetime) -> LocalAlert | None:
        existing = self._latest.get(result.symbol, {}).get(result.horizon)
        ignored = existing is not None and (
            result.analysis_id == existing.analysis_id or result.as_of < existing.as_of
        )
        alert = super().ingest(result, now=now)
        if ignored:
            return alert

        session = _market_session(result.as_of)
        if alert is not None:
            if alert.kind in {AlertKind.ENTRY_CONFIRMED, AlertKind.HIGH_CONVICTION_BUY}:
                self._swing_continuation_sessions.add((result.symbol, session))
                self._swing_continuation_candidates.pop(result.symbol, None)
            return alert
        if result.horizon is not AnalysisHorizon.INTRADAY:
            return None
        return self._confirm_swing_continuation(result, now=now, session=session)

    def _confirm_swing_continuation(
        self,
        result: AnalysisResult,
        *,
        now: datetime,
        session: date,
    ) -> LocalAlert | None:
        session_key = (result.symbol, session)
        if session_key in self._swing_continuation_sessions:
            return None
        fresh = self._fresh_values(result.symbol, now)
        swing = _bullish_swing(
            fresh.get(AnalysisHorizon.SWING),
            self._policy.watch_threshold,
        )
        if swing is None or not _valid_swing(swing) or not self._qualifies(result):
            return None

        candidate = self._swing_continuation_candidates.get(result.symbol)
        if candidate is None or (
            self._same_market_session_required and candidate[2] != session
        ):
            self._swing_continuation_candidates[result.symbol] = (
                result.analysis_id,
                result.as_of,
                session,
            )
            return None
        candidate_id, candidate_at, _ = candidate
        if result.analysis_id == candidate_id:
            return None
        if result.as_of - candidate_at < self._minimum_reconfirmation_delay:
            return None

        alert = self._build_named_alert(
            result.symbol,
            AlertKind.ENTRY_CONFIRMED,
            (swing, result),
            fresh,
            now,
        )
        if alert is None:
            return None
        self._swing_continuation_sessions.add(session_key)
        self._swing_continuation_candidates.pop(result.symbol, None)
        return alert.model_copy(
            update={
                "message": (
                    "Two fresh strong Intraday higher lows confirm the active Swing continuation"
                ),
                "reasons": tuple(
                    dict.fromkeys(
                        (
                            *alert.reasons,
                            "swing_continuation_confirmed",
                            "two_strong_intraday_higher_lows",
                            "fresh_intraday_reconfirmation",
                            "long_structure_not_required",
                        )
                    )
                ),
            }
        )

    def _qualifies(self, result: AnalysisResult) -> bool:
        metrics = _metrics(result)
        return (
            result.direction is PatternDirection.BULLISH
            and result.verdict in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}
            and metrics.get("setup") in _CONTINUATION_SETUPS
            and (
                not self._strong_confirmation_required
                or metrics.get("confirmation_quality") == "strong"
            )
            and (
                not self._five_minute_higher_low_required
                or metrics.get("five_minute_higher_low") is True
            )
        )


def _valid_swing(result: AnalysisResult) -> bool:
    metrics = _metrics(result)
    return (
        metrics.get("anchored_vwap_gate_passed") is True
        and metrics.get("classification") in {"breakout", "pullback", "extended"}
    )


def _bullish_swing(
    result: AnalysisResult | None,
    minimum: Decimal,
) -> AnalysisResult | None:
    if result is None or result.direction is not PatternDirection.BULLISH:
        return None
    if result.verdict not in {AnalysisVerdict.FAVORABLE, AnalysisVerdict.WATCH}:
        return None
    return result if result.score * result.confidence >= minimum else None


def _market_session(value: datetime) -> date:
    return value.astimezone(_NEW_YORK).date()


def _metrics(result: AnalysisResult) -> dict[str, object]:
    return {item.name: item.value for item in result.metrics}
