from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.alpaca_market_data.engine import AlpacaMarketDataEngine
from app.alpaca_market_data.normalizer import AlpacaEventNormalizer
from app.alpaca_market_data.replay import HistoricalMarketDataStream
from app.contracts import EventEnvelope, MarketBar


class FakeRest:
    def __init__(self, bars: dict[str, list[Mapping[str, object]]]) -> None:
        self.bars = bars
        self.calls: list[dict[str, object]] = []

    async def fetch_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[Mapping[str, object]]]:
        self.calls.append(
            {
                "symbols": symbols,
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "limit": limit,
            }
        )
        return self.bars

    async def fetch_snapshots(
        self, symbols: tuple[str, ...]
    ) -> dict[str, Mapping[str, object]]:
        raise AssertionError(f"snapshots are not part of replay: {symbols}")

    async def close(self) -> None:
        return None


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventEnvelope]] = []

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        self.events.append((subject, envelope))


def raw_bar(symbol: str, timestamp: str, *, volume: int) -> dict[str, Any]:
    return {
        "T": "b",
        "S": symbol,
        "t": timestamp,
        "o": "100",
        "h": "102",
        "l": "99",
        "c": "101",
        "v": volume,
        "n": 7,
        "vw": "100.5",
    }


@pytest.mark.asyncio
async def test_replay_merges_symbols_rebases_date_and_preserves_ohlcv() -> None:
    rest = FakeRest(
        {
            "MSFT": [raw_bar("MSFT", "2026-07-24T13:31:00Z", volume=200)],
            "AAPL": [
                raw_bar("AAPL", "2026-07-24T13:30:00Z", volume=100),
                raw_bar("AAPL", "2026-07-24T13:31:00Z", volume=150),
            ],
        }
    )
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    replay = HistoricalMarketDataStream(
        rest=rest,
        source_date=date(2026, 7, 24),
        simulated_date=date(2026, 8, 10),
        speed=60,
        run_id="run-001",
        sleep=sleep,
    )

    messages = [message async for message in replay.messages(("MSFT", "AAPL"))]

    assert [(item["t"], item["S"]) for item in messages] == [
        ("2026-08-10T13:30:00Z", "AAPL"),
        ("2026-08-10T13:31:00Z", "AAPL"),
        ("2026-08-10T13:31:00Z", "MSFT"),
    ]
    assert messages[0] == {
        **raw_bar("AAPL", "2026-07-24T13:30:00Z", volume=100),
        "t": "2026-08-10T13:30:00Z",
        "marketbot_replay_run_id": "run-001",
    }
    assert sleeps == [1.0]
    assert rest.calls == [
        {
            "symbols": ("MSFT", "AAPL"),
            "timeframe": "1Min",
            "start": datetime(2026, 7, 24, 13, 30, tzinfo=UTC),
            "end": datetime(2026, 7, 24, 20, 0, tzinfo=UTC),
            "limit": 10_000,
        }
    ]


@pytest.mark.asyncio
async def test_replay_can_run_without_wall_clock_delays_and_ignores_live_only_channels() -> None:
    rest = FakeRest(
        {"AAPL": [raw_bar("AAPL", "2026-01-05T14:30:00Z", volume=100)]}
    )
    replay = HistoricalMarketDataStream(
        rest=rest,
        source_date=date(2026, 1, 5),
        simulated_date=date(2026, 8, 10),
        speed=None,
    )

    messages = [
        message
        async for message in replay.messages(
            ("AAPL",),
            trades=False,
            quotes=False,
            bars=True,
            updated_bars=False,
            daily_bars=False,
        )
    ]

    assert len(messages) == 1
    assert messages[0]["t"] == "2026-08-10T13:30:00Z"


@pytest.mark.asyncio
async def test_replay_uses_the_same_normalized_market_bar_contract_as_live_stream() -> None:
    rest = FakeRest(
        {"AAPL": [raw_bar("AAPL", "2026-07-24T13:30:00Z", volume=1234)]}
    )
    publisher = RecordingPublisher()
    engine = AlpacaMarketDataEngine(
        rest=None,
        stream=HistoricalMarketDataStream(
            rest=rest,
            source_date=date(2026, 7, 24),
            simulated_date=date(2026, 8, 10),
            speed=None,
            run_id="contract-test",
        ),
        publisher=publisher,
        normalizer=AlpacaEventNormalizer(feed="sip-replay"),
    )

    count = await engine.stream_once(
        ("AAPL",),
        trades=False,
        quotes=False,
        updated_bars=False,
        daily_bars=False,
    )

    assert count == 1
    subject, envelope = publisher.events[0]
    assert subject == "marketbot.v1.market.bar.1Min.AAPL"
    assert isinstance(envelope.payload, MarketBar)
    assert envelope.payload.timestamp == datetime(2026, 8, 10, 13, 30, tzinfo=UTC)
    assert str(envelope.payload.volume) == "1234"
    assert envelope.payload.is_final is True


@pytest.mark.asyncio
async def test_replay_run_id_changes_event_identity_without_changing_market_bar() -> None:
    records = {"AAPL": [raw_bar("AAPL", "2026-07-24T13:30:00Z", volume=1234)]}

    async def publication(run_id: str) -> tuple[object, MarketBar]:
        publisher = RecordingPublisher()
        engine = AlpacaMarketDataEngine(
            rest=None,
            stream=HistoricalMarketDataStream(
                rest=FakeRest(records),
                source_date=date(2026, 7, 24),
                simulated_date=date(2026, 8, 10),
                speed=None,
                run_id=run_id,
            ),
            publisher=publisher,
            normalizer=AlpacaEventNormalizer(feed="sip-replay"),
        )
        await engine.stream_once(("AAPL",), trades=False, quotes=False)
        payload = publisher.events[0][1].payload
        assert isinstance(payload, MarketBar)
        return publisher.events[0][1].event_id, payload

    first_id, first_bar = await publication("run-001")
    second_id, second_bar = await publication("run-002")

    assert first_id != second_id
    assert first_bar == second_bar


def test_replay_rejects_same_or_future_source_date() -> None:
    rest = FakeRest({})

    with pytest.raises(ValueError, match="earlier than simulated_date"):
        HistoricalMarketDataStream(
            rest=rest,
            source_date=date(2026, 8, 10),
            simulated_date=date(2026, 8, 10),
            speed=60,
        )


@pytest.mark.asyncio
async def test_replay_requires_completed_bar_channel() -> None:
    replay = HistoricalMarketDataStream(
        rest=FakeRest({}),
        source_date=date(2026, 8, 7),
        simulated_date=date(2026, 8, 10),
        speed=60,
    )

    with pytest.raises(ValueError, match="completed minute bars"):
        _ = [
            message
            async for message in replay.messages(
                ("AAPL",),
                trades=True,
                quotes=True,
                bars=False,
                updated_bars=True,
                daily_bars=True,
            )
        ]
