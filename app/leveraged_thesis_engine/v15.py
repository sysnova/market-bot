"""Tactical SHORT confirmation uses only the inverse instrument's buyer regime."""

from datetime import timedelta
from decimal import Decimal

from app.contracts import OrderFlowStateKind, ShortGate

from .v11 import PricePoint
from .v14 import LeveragedThesisEngineV14, ShortObservationContext


class LeveragedThesisEngineV15(LeveragedThesisEngineV14):
    engine_version = "1.5.0"

    def _instrument_evidence(
        self,
        prices: tuple[PricePoint, ...],
        context: ShortObservationContext,
        instrument: str,
    ) -> tuple[tuple[PricePoint, ...], list[ShortGate], Decimal | None, str | None]:
        flow = context.instrument_flow
        current = bool(
            flow
            and flow.symbol == instrument
            and timedelta(0) <= context.now - flow.occurred_at <= timedelta(seconds=4)
        )
        buyer = bool(
            current
            and flow
            and flow.state in {OrderFlowStateKind.BUY_PRESSURE, OrderFlowStateKind.BUY_ABSORPTION}
        )
        gate = ShortGate(
            name="instrument_confirmation",
            passed=buyer,
            reason="passed"
            if buyer
            else (
                "instrument_buyer_regime_pending"
                if current
                else "instrument_flow_unavailable_or_stale"
            ),
        )
        return (), [gate], None, "buy_flow" if buyer else None
