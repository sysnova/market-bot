"""Input context for horizontal 4HGERI structure analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.contracts import (
    AnalysisResult,
    EntryMaturityLevel,
    GeriAssessment,
    MarketBar,
    SupportAssessment,
)
from app.contracts.order_flow_support import OrderFlowSupportAssessment


@dataclass(frozen=True, slots=True)
class Swing4HGeriContext:
    symbol: str
    bars: tuple[MarketBar, ...]
    current_price: Decimal
    confirmation_bars: tuple[MarketBar, ...] = ()
    daily_swing: AnalysisResult | None = None
    existing_maturity: EntryMaturityLevel | None = None
    active_structure: GeriAssessment | None = None
    support: SupportAssessment | None = None
    order_flow_support: OrderFlowSupportAssessment | None = None
    as_of: datetime | None = None
    current_price_at: datetime | None = None
