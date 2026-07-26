from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar


def trend_bars(
    *,
    symbol: str,
    start: Decimal,
    step: Decimal,
    final_move: Decimal,
    base_volume: Decimal,
    final_volume: Decimal,
    count: int = 60,
    timeframe: BarTimeframe = BarTimeframe.MINUTE_1,
) -> tuple[MarketBar, ...]:
    timestamp = datetime(2026, 7, 24, 13, 30, tzinfo=UTC)
    bars: list[MarketBar] = []
    previous = start
    interval = timedelta(minutes=1 if timeframe is BarTimeframe.MINUTE_1 else 5)
    for index in range(count):
        close = start + step * Decimal(index)
        if index == count - 1:
            close = previous + final_move
        open_price = previous
        high = max(open_price, close) + Decimal("0.05")
        low = min(open_price, close) - Decimal("0.05")
        volume = final_volume if index == count - 1 else base_volume
        bars.append(
            MarketBar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp + interval * index,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                trade_count=100 if index == count - 1 else 40,
                vwap=(open_price + close) / Decimal("2"),
                source="fixture",
                feed="sip",
            )
        )
        previous = close
    return tuple(bars)
