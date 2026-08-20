"""Input context for horizontal 4HGERI structure analysis."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contracts import AnalysisResult, EntryMaturityLevel, GeriAssessment, MarketBar


@dataclass(frozen=True, slots=True)
class Swing4HGeriContext:
    symbol: str
    bars: tuple[MarketBar, ...]
    current_price: Decimal
    confirmation_bars: tuple[MarketBar, ...] = ()
    daily_swing: AnalysisResult | None = None
    existing_maturity: EntryMaturityLevel | None = None
    active_structure: GeriAssessment | None = None
