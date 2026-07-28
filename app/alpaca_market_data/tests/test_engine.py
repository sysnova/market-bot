from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from app.alpaca_market_data.engine import AlpacaMarketDataEngine
from app.alpaca_market_data.normalizer import AlpacaEventNormalizer
from app.contracts import EventEnvelope


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


class FakeRest:
    async def fetch_bars(
        self, *args: object, **kwargs: object
    ) -> dict[str, list[dict[str, object]]]:
        return {
            "AAPL": [
                {
                    "o": 100,
                    "h": 101,
                    "l": 99,
                    "c": 100.5,
                    "v": 1000,
                    "t": "2026-07-24T14:30:00Z",
                }
            ]
        }

    async def fetch_snapshots(self, symbols: tuple[str, ...]) -> dict[str, dict[str, object]]:
        return {
            "AAPL": {
                "latestTrade": {"p": 100.5, "s": 1, "t": "2026-07-24T14:31:00Z"}
            }
        }


class FakeStream:
    async def messages(
        self, *args: object, **kwargs: object
    ) -> AsyncIterator[dict[str, object]]:
        yield {
            "T": "q",
            "S": "AAPL",
            "bp": 100.4,
            "bs": 2,
            "ap": 100.6,
            "as": 3,
            "t": "2026-07-24T14:31:00Z",
        }


class WeeklyFakeRest(FakeRest):
    async def fetch_bars(
        self, *args: object, **kwargs: object
    ) -> dict[str, list[dict[str, object]]]:
        return {
            "AAPL": [
                {
                    "o": 98,
                    "h": 101,
                    "l": 97,
                    "c": 100,
                    "v": 10_000,
                    "t": "2026-07-13T04:00:00Z",
                },
                {
                    "o": 100,
                    "h": 103,
                    "l": 99,
                    "c": 102,
                    "v": 12_000,
                    "t": "2026-07-20T04:00:00Z",
                },
            ]
        }


class BatchTrackingRest(FakeRest):
    def __init__(self) -> None:
        self.bar_calls: list[tuple[str, ...]] = []
        self.snapshot_calls: list[tuple[str, ...]] = []

    async def fetch_bars(
        self, symbols: tuple[str, ...], *args: object, **kwargs: object
    ) -> dict[str, list[dict[str, object]]]:
        self.bar_calls.append(symbols)
        return {
            symbol: [
                {
                    "o": 100,
                    "h": 101,
                    "l": 99,
                    "c": 100.5,
                    "v": 1000,
                    "t": "2026-07-24T14:30:00Z",
                }
            ]
            for symbol in symbols
        }

    async def fetch_snapshots(
        self, symbols: tuple[str, ...]
    ) -> dict[str, dict[str, object]]:
        self.snapshot_calls.append(symbols)
        return {
            symbol: {
                "latestTrade": {"p": 100.5, "s": 1, "t": "2026-07-24T14:31:00Z"}
            }
            for symbol in symbols
        }


class MultiBarRest(FakeRest):
    async def fetch_bars(
        self, symbols: tuple[str, ...], *args: object, **kwargs: object
    ) -> dict[str, list[dict[str, object]]]:
        return {
            symbol: [
                {
                    "o": 100 + minute,
                    "h": 101 + minute,
                    "l": 99 + minute,
                    "c": 100.5 + minute,
                    "v": 1000,
                    "t": f"2026-07-24T14:{30 + minute:02d}:00Z",
                }
                for minute in range(3)
            ]
            for symbol in symbols
        }


@pytest.mark.asyncio
async def test_engine_publishes_only_the_consumer_working_set() -> None:
    publisher = FakePublisher()
    engine = AlpacaMarketDataEngine(
        rest=MultiBarRest(),
        stream=FakeStream(),
        publisher=publisher,
        normalizer=AlpacaEventNormalizer(feed="sip"),
    )

    count = await engine.publish_bars(
        ("AAPL",),
        timeframe="15Min",
        start=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
        end=datetime(2026, 7, 24, 14, 33, tzinfo=UTC),
        max_bars_per_symbol=2,
    )

    assert count == 2
    assert [item.payload.timestamp.minute for _, item in publisher.events] == [31, 32]


@pytest.mark.asyncio
async def test_engine_batches_large_rest_universes() -> None:
    publisher = FakePublisher()
    rest = BatchTrackingRest()
    engine = AlpacaMarketDataEngine(
        rest=rest,
        stream=FakeStream(),
        publisher=publisher,
        normalizer=AlpacaEventNormalizer(feed="sip"),
        rest_batch_size=2,
    )

    bar_count = await engine.publish_bars(
        ("AAPL", "MSFT", "NVDA", "AAPL"),
        timeframe="15Min",
        start=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
        end=datetime(2026, 7, 24, 14, 31, tzinfo=UTC),
    )
    snapshot_count = await engine.publish_snapshots(("AAPL", "MSFT", "NVDA", "AAPL"))

    assert rest.bar_calls == [("AAPL", "MSFT"), ("NVDA",)]
    assert rest.snapshot_calls == [("AAPL", "MSFT"), ("NVDA",)]
    assert (bar_count, snapshot_count) == (3, 3)


def test_engine_rejects_invalid_rest_batch_size() -> None:
    with pytest.raises(ValueError, match="batch size"):
        AlpacaMarketDataEngine(
            rest=FakeRest(),
            stream=FakeStream(),
            publisher=FakePublisher(),
            normalizer=AlpacaEventNormalizer(feed="sip"),
            rest_batch_size=0,
        )


@pytest.mark.asyncio
async def test_engine_publishes_backfill_snapshots_and_stream_without_orders() -> None:
    publisher = FakePublisher()
    engine = AlpacaMarketDataEngine(
        rest=FakeRest(),
        stream=FakeStream(),
        publisher=publisher,
        normalizer=AlpacaEventNormalizer(feed="sip"),
    )

    bars = await engine.publish_bars(
        ("AAPL",),
        timeframe="1Min",
        start=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
        end=datetime(2026, 7, 24, 14, 31, tzinfo=UTC),
    )
    snapshots = await engine.publish_snapshots(("AAPL",))
    streamed = await engine.stream_once(("AAPL",))

    assert (bars, snapshots, streamed) == (1, 1, 1)
    assert [subject for subject, _ in publisher.events] == [
        "marketbot.v1.market.bar.1Min.AAPL",
        "market.data.snapshot.aapl",
        "market.data.quote.aapl",
    ]
    assert not hasattr(engine, "submit_order")


@pytest.mark.asyncio
async def test_engine_excludes_the_current_week_until_it_is_complete() -> None:
    publisher = FakePublisher()
    engine = AlpacaMarketDataEngine(
        rest=WeeklyFakeRest(),
        stream=FakeStream(),
        publisher=publisher,
        normalizer=AlpacaEventNormalizer(feed="sip"),
    )

    friday_count = await engine.publish_bars(
        ("AAPL",),
        timeframe="1Week",
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 7, 24, 20, 0, tzinfo=UTC),
    )

    assert friday_count == 1
    assert len(publisher.events) == 1

    publisher.events.clear()
    saturday_count = await engine.publish_bars(
        ("AAPL",),
        timeframe="1Week",
        start=datetime(2026, 7, 1, tzinfo=UTC),
        end=datetime(2026, 7, 25, 6, 0, tzinfo=UTC),
    )

    assert saturday_count == 2
    assert len(publisher.events) == 2
