"""Bounded in-memory OHLCV history used by the analytical composition root."""

from bisect import bisect_left

from app.contracts import BarTimeframe, MarketBar


class MarketBarStore:
    """Keep independent, timestamp-ordered symbol/timeframe series."""

    def __init__(self, *, capacity_per_series: int = 2_000) -> None:
        if capacity_per_series < 1:
            raise ValueError("capacity_per_series must be positive")
        self._capacity = capacity_per_series
        self._series: dict[tuple[str, BarTimeframe], list[MarketBar]] = {}

    def add(self, bar: MarketBar) -> None:
        key = (bar.symbol.upper(), bar.timeframe)
        series = self._series.setdefault(key, [])
        timestamps = [item.timestamp for item in series]
        index = bisect_left(timestamps, bar.timestamp)
        if index < len(series) and series[index].timestamp == bar.timestamp:
            series[index] = bar
        else:
            series.insert(index, bar)
        overflow = len(series) - self._capacity
        if overflow > 0:
            del series[:overflow]

    def history(
        self,
        symbol: str,
        timeframe: BarTimeframe,
        *,
        limit: int | None = None,
        final_only: bool = False,
    ) -> tuple[MarketBar, ...]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        values = tuple(self._series.get((symbol.upper(), timeframe), ()))
        if final_only:
            values = tuple(item for item in values if item.is_final)
        return values[-limit:] if limit is not None else values
