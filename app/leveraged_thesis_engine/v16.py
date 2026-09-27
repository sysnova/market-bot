"""Daily and tactical SHORT use only the inverse ETF's current buyer regime."""

from datetime import datetime, timedelta
from typing import Literal

from app.common.market_session import is_regular_session
from app.contracts import LeveragedThesisState

from .v11 import DeferredShortState
from .v14 import ShortObservationContext
from .v15 import LeveragedThesisEngineV15


class LeveragedThesisEngineV16(LeveragedThesisEngineV15):
    engine_version = "1.6.0"
    short_confirmation_basis: Literal["EXECUTABLE_QUOTE", "BUYER_REGIME"] = "BUYER_REGIME"

    def _pending_status(
        self, state: DeferredShortState, now: datetime
    ) -> tuple[LeveragedThesisState, str]:
        assert state.intent is not None
        intent, underlying = state.intent, state.underlying
        armed, cancelled = LeveragedThesisState.STRUCTURE_ARMED, LeveragedThesisState.CANCELLED
        if (
            underlying is not None
            and underlying.occurred_at >= intent.alert.created_at
            and underlying.current_price >= intent.invalidation
        ):
            return cancelled, "short_published_invalidation_reached"
        if now >= intent.expires_at:
            return cancelled, "short_session_expired"
        if not is_regular_session(now):
            return armed, "regular_session_required"
        if (
            underlying is None
            or not intent.alert.created_at <= underlying.occurred_at <= now
            or now - underlying.occurred_at > timedelta(minutes=1)
        ):
            return armed, "underlying_price_pending_or_stale"
        _, gates, _, _ = self._instrument_evidence(
            (),
            ShortObservationContext(
                symbol=intent.alert.symbol, now=now, instrument_flow=state.instrument
            ),
            intent.instrument,
        )
        if gates[0].passed:
            return LeveragedThesisState.BUY_CONFIRMED, "instrument_buy_flow_confirmed"
        return armed, gates[0].reason
