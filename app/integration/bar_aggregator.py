"""Deterministic aggregation of completed one-minute bars."""

from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.common.market_session import is_regular_session, market_session
from app.contracts import BarTimeframe, MarketBar, MarketSession

_TARGET_MINUTES = {
    BarTimeframe.MINUTE_5: 5,
    BarTimeframe.MINUTE_15: 15,
    BarTimeframe.HOUR_1: 60,
}
_NEW_YORK = ZoneInfo("America/New_York")


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
        if not is_regular_session(bar.timestamp):
            return (
                self._flush(bar.symbol)
                if market_session(bar.timestamp) is MarketSession.AFTER_HOURS
                else ()
            )
        emitted: list[MarketBar] = []
        for target in self._targets:
            key = (bar.symbol, target)
            bucket_start = _bucket_start(bar.timestamp, _TARGET_MINUTES[target])
            pending = self._pending.get(key)
            if pending is None:
                self._pending[key] = [bar]
                continue
            if pending[-1].timestamp.date() != bar.timestamp.date():
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

    def _flush(self, symbol: str) -> tuple[MarketBar, ...]:
        emitted: list[MarketBar] = []
        for target in self._targets:
            pending = self._pending.pop((symbol, target), None)
            if pending is None:
                continue
            start = _bucket_start(pending[0].timestamp, _TARGET_MINUTES[target])
            if pending[0].timestamp == start:
                emitted.append(_aggregate(pending, target, start))
        return tuple(emitted)


class RegularSessionDailyAggregator:
    """Build a completed daily bar from the full 09:30-16:00 ET minute session."""

    def __init__(self) -> None:
        self._pending: dict[str, list[MarketBar]] = {}

    def add(self, bar: MarketBar) -> MarketBar | None:
        if bar.timeframe is not BarTimeframe.MINUTE_1:
            raise ValueError("daily aggregation input must use 1Min timeframe")
        if not bar.is_final or not is_regular_session(bar.timestamp):
            return None
        local = bar.timestamp.astimezone(_NEW_YORK)
        pending = self._pending.get(bar.symbol)
        if pending is None or pending[-1].timestamp.astimezone(_NEW_YORK).date() != local.date():
            pending = []
            self._pending[bar.symbol] = pending
        pending.append(bar)
        if local.time() != time(15, 59):
            return None
        values = self._pending.pop(bar.symbol)
        if values[0].timestamp.astimezone(_NEW_YORK).time() != time(9, 30):
            return None
        timestamp = datetime.combine(local.date(), time(), _NEW_YORK).astimezone(UTC)
        return _aggregate(values, BarTimeframe.DAY_1, timestamp)


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
