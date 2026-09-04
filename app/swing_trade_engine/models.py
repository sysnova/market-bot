"""Pure input context for Fibonacci SwingTrade evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.contracts.geri_4h import GeriAssessment
from app.contracts.market_analysis import MarketBar
from app.contracts.order_flow_support import OrderFlowSupportAssessment
from app.contracts.support_confirmation import SupportAssessment


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
    four_hour_bars: tuple[MarketBar, ...] = ()
    momentum_daily_bars: tuple[MarketBar, ...] | None = None
