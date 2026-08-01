"""Deterministic aggregation of completed one-minute bars."""

from datetime import datetime
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar

_TARGET_MINUTES = {
    BarTimeframe.MINUTE_5: 5,
    BarTimeframe.MINUTE_15: 15,
    BarTimeframe.HOUR_1: 60,
}


class MinuteBarAggregator:
    """Emit a target bar when the first minute of the next bucket arrives."""

    def __init__(self, *, targets: tuple[BarTimeframe, ...]) -> None:
        if not targets or len(targets) != len(set(targets)):
            raise ValueError("aggregation targets must be non-empty and unique")
        if any(target not in _TARGET_MINUTES for target in targets):
            raise ValueError("only 5Min, 15Min and 1Hour aggregation is supported")
        self._targets = targets
        self._pending: dict[tuple[str, BarTimeframe], list[MarketBar]] = {}

    def add(self, bar: MarketBar) -> tuple[MarketBar, ...]:
        if bar.timeframe is not BarTimeframe.MINUTE_1:
            raise ValueError("aggregation input must use 1Min timeframe")
        if not bar.is_final:
            return ()
        emitted: list[MarketBar] = []
        for target in self._targets:
            key = (bar.symbol, target)
            bucket_start = _bucket_start(bar.timestamp, _TARGET_MINUTES[target])
            pending = self._pending.get(key)
            if pending is None:
                self._pending[key] = [bar]
                continue
            current_start = _bucket_start(pending[0].timestamp, _TARGET_MINUTES[target])
            if bucket_start < current_start:
                continue
            if bucket_start == current_start:
                pending.append(bar)
                continue
            if pending[0].timestamp == current_start:
                emitted.append(_aggregate(pending, target, current_start))
            self._pending[key] = [bar]
        return tuple(emitted)


def _bucket_start(timestamp: datetime, minutes: int) -> datetime:
    return timestamp.replace(
        minute=(timestamp.minute // minutes) * minutes,
        second=0,
        microsecond=0,
    )


def _aggregate(
    bars: list[MarketBar],
    timeframe: BarTimeframe,
    timestamp: datetime,
) -> MarketBar:
    total_volume = sum((bar.volume for bar in bars), start=Decimal("0"))
    if total_volume > 0:
        weighted = sum(
            ((bar.vwap or bar.close) * bar.volume for bar in bars),
            start=Decimal("0"),
        )
        vwap = weighted / total_volume
    else:
        vwap = bars[-1].close
    trade_counts = tuple(bar.trade_count for bar in bars)
    trade_count = (
        sum(value for value in trade_counts if value is not None)
        if any(value is not None for value in trade_counts)
        else None
    )
    return MarketBar(
        symbol=bars[0].symbol,
        timeframe=timeframe,
        timestamp=timestamp,
        open=bars[0].open,
        high=max(bar.high for bar in bars),
        low=min(bar.low for bar in bars),
        close=bars[-1].close,
        volume=total_volume,
        trade_count=trade_count,
        vwap=vwap,
        source="marketbot-aggregator",
        feed=bars[0].feed,
        is_final=True,
    )
