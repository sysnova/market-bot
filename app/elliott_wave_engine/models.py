"""Immutable inputs owned by the Elliott Wave engine."""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import BarTimeframe, MarketBar


@dataclass(frozen=True, slots=True)
class WaveContext:
    symbol: str
    daily_bars: tuple[MarketBar, ...]
    hourly_bars: tuple[MarketBar, ...] = ()

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("wave context requires a symbol")
        if any(
            bar.symbol.strip().upper() != symbol
            for bar in (*self.daily_bars, *self.hourly_bars)
        ):
            raise ValueError("all wave bars must belong to the context symbol")
        if any(bar.timeframe is not BarTimeframe.DAY_1 for bar in self.daily_bars):
            raise ValueError("daily_bars must contain only 1Day bars")
        if any(bar.timeframe is not BarTimeframe.HOUR_1 for bar in self.hourly_bars):
            raise ValueError("hourly_bars must contain only 1Hour bars")
