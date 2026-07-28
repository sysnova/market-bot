"""Application service publishing normalized Alpaca market data."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.contracts import BarTimeframe, MarketBar

from .normalizer import AlpacaEventNormalizer, Publication
from .ports import EventPublisher, MarketDataRest, MarketDataStream

_NEW_YORK = ZoneInfo("America/New_York")


class AlpacaMarketDataEngine:
    """Read-only ingress: Alpaca data in, immutable events out."""

    def __init__(
        self,
        *,
        rest: MarketDataRest,
        stream: MarketDataStream,
        publisher: EventPublisher,
        backfill_publisher: EventPublisher | None = None,
        normalizer: AlpacaEventNormalizer,
        rest_batch_size: int = 20,
    ) -> None:
        if isinstance(rest_batch_size, bool) or rest_batch_size < 1:
            raise ValueError("Alpaca REST batch size must be positive")
        self._rest = rest
        self._stream = stream
        self._publisher = publisher
        self._backfill_publisher = (
            publisher if backfill_publisher is None else backfill_publisher
        )
        self._normalizer = normalizer
        self._rest_batch_size = rest_batch_size

    async def publish_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
        max_bars_per_symbol: int | None = None,
    ) -> int:
        if max_bars_per_symbol is not None and (
            isinstance(max_bars_per_symbol, bool) or max_bars_per_symbol < 1
        ):
            raise ValueError("max_bars_per_symbol must be positive")
        count = 0
        for batch in _symbol_batches(symbols, self._rest_batch_size):
            bars = await self._rest.fetch_bars(
                batch,
                timeframe=timeframe,
                start=start,
                end=end,
                limit=limit,
            )
            for symbol in sorted(bars):
                records = bars[symbol]
                if max_bars_per_symbol is not None:
                    records = records[-max_bars_per_symbol:]
                for raw in records:
                    publication = self._normalizer.rest_bar(symbol, timeframe, raw)
                    payload = publication.envelope.payload
                    if (
                        timeframe == BarTimeframe.WEEK_1.value
                        and isinstance(payload, MarketBar)
                        and not _weekly_bar_is_complete(payload.timestamp, end)
                    ):
                        continue
                    await self._publish_to(self._backfill_publisher, publication)
                    count += 1
        return count

    async def publish_snapshots(self, symbols: tuple[str, ...]) -> int:
        count = 0
        for batch in _symbol_batches(symbols, self._rest_batch_size):
            snapshots = await self._rest.fetch_snapshots(batch)
            for symbol in sorted(snapshots):
                await self._publish(self._normalizer.snapshot(symbol, snapshots[symbol]))
                count += 1
        return count

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
        await self._publish_to(self._publisher, publication)

    @staticmethod
    async def _publish_to(
        publisher: EventPublisher, publication: Publication
    ) -> None:
        await publisher.publish(publication.subject, publication.envelope)


def _weekly_bar_is_complete(timestamp: datetime, as_of: datetime) -> bool:
    local_date = timestamp.astimezone(_NEW_YORK).date()
    week_start = local_date - timedelta(days=local_date.weekday())
    completion = datetime.combine(week_start + timedelta(days=5), time(), _NEW_YORK)
    return as_of.astimezone(_NEW_YORK) >= completion


def _symbol_batches(
    symbols: tuple[str, ...], batch_size: int
) -> tuple[tuple[str, ...], ...]:
    normalized = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if not normalized or any(not symbol for symbol in normalized):
        raise ValueError("at least one non-blank symbol is required")
    return tuple(
        normalized[index : index + batch_size]
        for index in range(0, len(normalized), batch_size)
    )
