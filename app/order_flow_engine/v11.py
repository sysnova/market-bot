"""Order Flow 1.1 with executable L1 quote evidence in its assessment."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.contracts.order_flow import MarketQuote

from .engine import OrderFlowEngine, OrderFlowPolicy


class OrderFlowEngineV11(OrderFlowEngine):
    """Publish bid, ask and spread without exposing the raw WebSocket downstream."""

    engine_version = "1.1.0"

    def __init__(self, policy: OrderFlowPolicy | None = None) -> None:
        super().__init__(policy)
        if not self.tracked_symbols:
            raise ValueError("Order Flow 1.1 requires a bounded tracked-symbol scope")

    def _quote_evidence(
        self, quote: MarketQuote | None
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        if quote is None:
            return None, None, None
        spread_bps = (quote.spread / quote.mid_price * Decimal("10000")).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        return quote.bid_price, quote.ask_price, spread_bps
