"""Order Flow 1.2 with confirmed state transitions and explicit pulse telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.contracts.order_flow import OrderFlowStateKind, OrderFlowWindow

from .engine import OrderFlowPolicy, StateMetadata
from .v11 import OrderFlowEngineV11

_BULLISH_STATES = {
    OrderFlowStateKind.BUY_PRESSURE,
    OrderFlowStateKind.SELLER_EXHAUSTION,
    OrderFlowStateKind.BUY_ABSORPTION,
    OrderFlowStateKind.BULLISH_DIVERGENCE,
}
_BEARISH_STATES = {
    OrderFlowStateKind.SELL_PRESSURE,
    OrderFlowStateKind.BUYER_EXHAUSTION,
    OrderFlowStateKind.SELL_ABSORPTION,
    OrderFlowStateKind.BEARISH_DIVERGENCE,
}


@dataclass(slots=True)
class _StateStability:
    stable_state: OrderFlowStateKind = OrderFlowStateKind.NEUTRAL
    stable_since: datetime | None = None
    pulse_state: OrderFlowStateKind = OrderFlowStateKind.NEUTRAL
    candidate_state: OrderFlowStateKind | None = None
    candidate_since: datetime | None = None
    candidate_samples: int = 0


class OrderFlowEngineV12(OrderFlowEngineV11):
    """Require persistent evidence before changing the downstream operational state."""

    engine_version = "1.2.0"

    def __init__(self, policy: OrderFlowPolicy | None = None) -> None:
        super().__init__(policy)
        self._stability: dict[str, _StateStability] = {}

    def reset_symbol(self, symbol: str) -> None:
        super().reset_symbol(symbol)
        self._stability.pop(symbol, None)

    def _stabilize_state(
        self,
        symbol: str,
        as_of: datetime,
        pulse_state: OrderFlowStateKind,
        *,
        advance: bool,
    ) -> OrderFlowStateKind:
        stability = self._stability.setdefault(symbol, _StateStability())
        stability.pulse_state = pulse_state
        if stability.stable_since is None:
            stability.stable_since = as_of
        if not advance:
            return stability.stable_state
        if pulse_state is stability.stable_state:
            self._clear_candidate(stability)
            return stability.stable_state
        if stability.candidate_state is pulse_state:
            stability.candidate_samples += 1
        else:
            stability.candidate_state = pulse_state
            stability.candidate_since = as_of
            stability.candidate_samples = 1

        required_samples, required_seconds = self._confirmation_gate(
            stability.stable_state,
            pulse_state,
        )
        candidate_since = stability.candidate_since
        if candidate_since is None:
            raise RuntimeError("Order Flow candidate state requires a start time")
        elapsed = Decimal(str((as_of - candidate_since).total_seconds()))
        if stability.candidate_samples < required_samples or elapsed < required_seconds:
            return stability.stable_state

        stability.stable_state = pulse_state
        stability.stable_since = as_of
        self._clear_candidate(stability)
        return stability.stable_state

    def _state_metadata(self, symbol: str) -> StateMetadata:
        stability = self._stability[symbol]
        return {
            "pulse_state": stability.pulse_state,
            "candidate_state": stability.candidate_state,
            "candidate_samples": stability.candidate_samples,
            "state_stable_since": stability.stable_since,
        }

    def _state_confidence(
        self,
        state: OrderFlowStateKind,
        pulse_state: OrderFlowStateKind,
        window: OrderFlowWindow,
        data_quality: Decimal,
    ) -> Decimal:
        confidence = self._confidence(window, data_quality)
        if state is pulse_state or _same_bias(state, pulse_state):
            return confidence
        return min(confidence, Decimal("0.49"))

    def _confirmation_gate(
        self,
        stable_state: OrderFlowStateKind,
        candidate_state: OrderFlowStateKind,
    ) -> tuple[int, Decimal]:
        if candidate_state is OrderFlowStateKind.NEUTRAL:
            return (
                self._policy.neutral_confirmation_samples,
                self._policy.neutral_confirmation_seconds,
            )
        if _opposite_bias(stable_state, candidate_state):
            return (
                self._policy.reversal_confirmation_samples,
                self._policy.reversal_confirmation_seconds,
            )
        return (
            self._policy.transition_confirmation_samples,
            self._policy.transition_confirmation_seconds,
        )

    @staticmethod
    def _clear_candidate(stability: _StateStability) -> None:
        stability.candidate_state = None
        stability.candidate_since = None
        stability.candidate_samples = 0


def _opposite_bias(
    stable_state: OrderFlowStateKind,
    candidate_state: OrderFlowStateKind,
) -> bool:
    return (stable_state in _BULLISH_STATES and candidate_state in _BEARISH_STATES) or (
        stable_state in _BEARISH_STATES and candidate_state in _BULLISH_STATES
    )


def _same_bias(first: OrderFlowStateKind, second: OrderFlowStateKind) -> bool:
    return (first in _BULLISH_STATES and second in _BULLISH_STATES) or (
        first in _BEARISH_STATES and second in _BEARISH_STATES
    )
