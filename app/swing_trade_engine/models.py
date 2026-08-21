"""Pure input context for Fibonacci SwingTrade evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.contracts import GeriAssessment, MarketBar


@dataclass(frozen=True, slots=True)
class SwingTradeContext:
    symbol: str
    as_of: datetime
    current_price: Decimal
    daily_bars: tuple[MarketBar, ...]
    geri: GeriAssessment | None = None
