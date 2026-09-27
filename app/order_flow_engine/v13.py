"""Independent quote snapshots without altering causal trade classifications."""

from datetime import datetime, timedelta

from app.contracts import ExecutionQuoteSnapshot, MarketQuote

from .engine import OrderFlowPolicy
from .v12 import OrderFlowEngineV12


class OrderFlowEngineV13(OrderFlowEngineV12):
    engine_version = "1.3.0"

    def __init__(self, policy: OrderFlowPolicy | None = None) -> None:
        super().__init__(policy)
        self._quote_cursors: dict[str, tuple[datetime, datetime]] = {}

    def execution_quote(
        self, quote: MarketQuote, *, now: datetime
    ) -> ExecutionQuoteSnapshot | None:
        if quote.symbol not in self.tracked_symbols or quote.received_at > now:
            return None
        previous = self._quote_cursors.get(quote.symbol)
        if previous is not None and (
            quote.occurred_at <= previous[0] or now - previous[1] < timedelta(seconds=1)
        ):
            return None
        self._quote_cursors[quote.symbol] = (quote.occurred_at, now)
        return ExecutionQuoteSnapshot(quote=quote, published_at=now)
