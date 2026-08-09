"""Durable Alert v3 checkpointing while preserving v3 for replay."""

from __future__ import annotations

from datetime import date, timedelta

from app.contracts import AnalysisResult

from .policy import AlertPolicy
from .state import (
    AlertEngineV3State,
    SwingContinuationCandidate,
    SwingContinuationSession,
)
from .v3 import AlertEngineV3


class AlertEngineV31(AlertEngineV3):
    """Restore and expose the bounded state required by Swing continuation."""

    engine_version = "3.1.0"

    def __init__(
        self,
        policy: AlertPolicy | None = None,
        *,
        minimum_reconfirmation_delay: timedelta = timedelta(minutes=3),
        strong_confirmation_required: bool = True,
        five_minute_higher_low_required: bool = True,
        same_market_session_required: bool = True,
        restored_state: AlertEngineV3State | None = None,
    ) -> None:
        super().__init__(
            policy,
            minimum_reconfirmation_delay=minimum_reconfirmation_delay,
            strong_confirmation_required=strong_confirmation_required,
            five_minute_higher_low_required=five_minute_higher_low_required,
            same_market_session_required=same_market_session_required,
        )
        if restored_state is not None:
            self._restore_state(restored_state)

    def snapshot_state(self) -> AlertEngineV3State:
        """Return a JSON-serializable checkpoint after the latest decision."""

        latest = tuple(
            result.model_dump(mode="json")
            for symbol in sorted(self._latest)
            for _, result in sorted(
                self._latest[symbol].items(),
                key=lambda item: item[0].value,
            )
        )
        candidates = tuple(
            SwingContinuationCandidate(
                symbol=symbol,
                analysis_id=value[0],
                observed_at=value[1],
                market_session=value[2],
            )
            for symbol, value in sorted(self._swing_continuation_candidates.items())
        )
        latest_session_by_symbol: dict[str, date] = {}
        for symbol, session in self._swing_continuation_sessions:
            previous = latest_session_by_symbol.get(symbol)
            if previous is None or session > previous:
                latest_session_by_symbol[symbol] = session
        sessions = tuple(
            SwingContinuationSession(symbol=symbol, market_session=session)
            for symbol, session in sorted(latest_session_by_symbol.items())
        )
        return AlertEngineV3State(
            latest_analyses=latest,
            continuation_candidates=candidates,
            continuation_sessions=sessions,
        )

    def _restore_state(self, state: AlertEngineV3State) -> None:
        for payload in state.latest_analyses:
            result = AnalysisResult.model_validate(payload, strict=False)
            self._latest.setdefault(result.symbol, {})[result.horizon] = result
        self._swing_continuation_candidates = {
            item.symbol: (item.analysis_id, item.observed_at, item.market_session)
            for item in state.continuation_candidates
        }
        self._swing_continuation_sessions = {
            (item.symbol, item.market_session) for item in state.continuation_sessions
        }
