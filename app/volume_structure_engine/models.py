"""Immutable inputs owned by Volume Structure."""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import AnalysisResult, BarTimeframe, MarketBar


@dataclass(frozen=True, slots=True)
class VolumeStructureContext:
    symbol: str
    weekly_bars: tuple[MarketBar, ...]
    previous_result: AnalysisResult | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("Volume Structure requires a symbol")
        if any(bar.symbol != symbol for bar in self.weekly_bars):
            raise ValueError("all Volume Structure bars must belong to the symbol")
        if any(bar.timeframe is not BarTimeframe.WEEK_1 for bar in self.weekly_bars):
            raise ValueError("Volume Structure requires weekly bars")
        timestamps = tuple(bar.timestamp for bar in self.weekly_bars)
        if timestamps != tuple(sorted(timestamps)) or len(timestamps) != len(set(timestamps)):
            raise ValueError("Volume Structure bars must be unique and ordered")
        if self.previous_result is not None and self.previous_result.symbol != symbol:
            raise ValueError("previous Volume Structure result belongs to another symbol")
