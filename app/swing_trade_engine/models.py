"""Pure input context for Fibonacci SwingTrade evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.contracts import GeriAssessment, MarketBar, SupportAssessment
from app.contracts.order_flow_support import OrderFlowSupportAssessment


@dataclass(frozen=True, slots=True)
class SwingTradeContext:
    symbol: str
    as_of: datetime
    current_price: Decimal
    daily_bars: tuple[MarketBar, ...]
    geri: GeriAssessment | None = None
    support: SupportAssessment | None = None
    order_flow_support: OrderFlowSupportAssessment | None = None
    confirmation_bars: tuple[MarketBar, ...] = ()
    current_price_at: datetime | None = None
