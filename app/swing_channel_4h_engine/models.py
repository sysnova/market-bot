"""Input context for independent four-hour Swing channel analysis."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contracts import (
    AnalysisResult,
    EntryMaturityLevel,
    MarketBar,
    SwingChannelAssessment,
)


@dataclass(frozen=True, slots=True)
class SwingChannel4HContext:
    symbol: str
    bars: tuple[MarketBar, ...]
    current_price: Decimal
    daily_swing: AnalysisResult | None = None
    existing_maturity: EntryMaturityLevel | None = None
    active_channel: SwingChannelAssessment | None = None
