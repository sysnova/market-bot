"""Bounded in-memory OHLCV history used by the analytical composition root."""

from bisect import bisect_left
from weakref import finalize

from app.contracts import BarTimeframe, MarketBar

from .ticker_cache_transport import shared_cache_client


class MarketBarStore:
    """Keep independent, timestamp-ordered symbol/timeframe series."""

    def __init__(self, *, capacity_per_series: int = 2_000) -> None:
        if capacity_per_series < 1:
            raise ValueError("capacity_per_series must be positive")
        self._capacity = capacity_per_series
        self._series: dict[tuple[str, BarTimeframe], list[MarketBar]] = {}
        self._cache = shared_cache_client()
        self._view = self._cache.view(capacity_per_series) if self._cache else None
        if self._cache is not None and self._view is not None:
            cleanup = finalize(self, self._cache.close_view, self._view)
            cleanup.atexit = False
        self._pending: list[list[object]] = []

    def add(self, bar: MarketBar) -> None:
        if self._cache is not None:
            self._pending.append(
                [
                    bar.symbol.upper(),
                    bar.timeframe.value,
                    bar.timestamp.timestamp(),
                    bar.is_final,
                    bar.model_dump_json(),
                ]
            )
            if len(self._pending) >= 256:
                self._flush()
            return
        key = (bar.symbol.upper(), bar.timeframe)
        series = self._series.setdefault(key, [])
        if not series or bar.timestamp > series[-1].timestamp:
            series.append(bar)
        elif bar.timestamp == series[-1].timestamp:
            series[-1] = bar
        else:
            index = bisect_left(series, bar.timestamp, key=lambda item: item.timestamp)
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
        if self._cache is not None:
            self._flush()
            return tuple(
                MarketBar.model_validate_json(value)
                for value in self._cache.call(
                    "history", self._view, symbol.upper(), timeframe.value, limit, final_only
                )
            )
        values = tuple(self._series.get((symbol.upper(), timeframe), ()))
        if final_only:
            values = tuple(item for item in values if item.is_final)
        return values[-limit:] if limit is not None else values

    def _flush(self) -> None:
        if self._cache is not None and self._pending:
            self._cache.call("add", self._view, self._pending)
            self._pending.clear()

    def retain_symbols(self, symbols: tuple[str, ...]) -> None:
        """Release histories no longer in this engine's effective universe."""
        allowed = {symbol.upper() for symbol in symbols}
        if self._cache is not None:
            self._flush()
            self._cache.call("retain_symbols", self._view, sorted(allowed))
        else:
            self._series = {
                key: values for key, values in self._series.items() if key[0] in allowed
            }
