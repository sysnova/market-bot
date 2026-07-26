"""Application service publishing normalized Alpaca market data."""

from __future__ import annotations

from datetime import datetime

from .normalizer import AlpacaEventNormalizer, Publication
from .ports import EventPublisher, MarketDataRest, MarketDataStream


class AlpacaMarketDataEngine:
    """Read-only ingress: Alpaca data in, immutable events out."""

    def __init__(
        self,
        *,
        rest: MarketDataRest,
        stream: MarketDataStream,
        publisher: EventPublisher,
        normalizer: AlpacaEventNormalizer,
    ) -> None:
        self._rest = rest
        self._stream = stream
        self._publisher = publisher
        self._normalizer = normalizer

    async def publish_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> int:
        bars = await self._rest.fetch_bars(
            symbols,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
        )
        count = 0
        for symbol in sorted(bars):
            for raw in bars[symbol]:
                await self._publish(self._normalizer.rest_bar(symbol, timeframe, raw))
                count += 1
        return count

    async def publish_snapshots(self, symbols: tuple[str, ...]) -> int:
        snapshots = await self._rest.fetch_snapshots(symbols)
        for symbol in sorted(snapshots):
            await self._publish(self._normalizer.snapshot(symbol, snapshots[symbol]))
        return len(snapshots)

    async def stream_once(
        self,
        symbols: tuple[str, ...],
        *,
        trades: bool = True,
        quotes: bool = True,
        bars: bool = True,
        updated_bars: bool = True,
        daily_bars: bool = True,
    ) -> int:
        count = 0
        async for raw in self._stream.messages(
            symbols,
            trades=trades,
            quotes=quotes,
            bars=bars,
            updated_bars=updated_bars,
            daily_bars=daily_bars,
        ):
            await self._publish(self._normalizer.stream_message(raw))
            count += 1
        return count

    async def close(self) -> None:
        """Release the owned REST transport; stream sessions close themselves."""

        await self._rest.close()

    async def _publish(self, publication: Publication) -> None:
        await self._publisher.publish(publication.subject, publication.envelope)
