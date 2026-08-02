"""Immutable inputs owned by Signal Fusion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.contracts import (
    AnalysisResult,
    FusionAssessment,
    PatreonCapsAssessment,
    SupportAssessment,
    WaveAssessment,
)


@dataclass(frozen=True, slots=True)
class SignalFusionContext:
    symbol: str
    support: SupportAssessment | None
    wave: WaveAssessment | None
    analyses: tuple[AnalysisResult, ...]
    patreon: PatreonCapsAssessment | None = None
    holding_quantity: Decimal = Decimal()
    previous_assessment: FusionAssessment | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("Signal Fusion requires a symbol")
        sources = (
            self.support,
            self.wave,
            self.patreon,
            *self.analyses,
        )
        if any(item is not None and item.symbol != symbol for item in sources):
            raise ValueError("all fusion inputs must belong to the context symbol")
        horizons = tuple(item.horizon for item in self.analyses)
        if len(horizons) != len(set(horizons)):
            raise ValueError("fusion analyses must be unique by horizon")
        if self.holding_quantity < Decimal():
            raise ValueError("holding quantity cannot be negative")
        if self.support is None and self.wave is None:
            raise ValueError("Signal Fusion requires support or Elliott price context")
