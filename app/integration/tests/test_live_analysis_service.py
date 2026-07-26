from datetime import UTC, datetime
from typing import Any

import pytest

from app.integration.live_analysis_service import LiveAnalysisService

NOW = datetime(2026, 7, 26, 15, 0, tzinfo=UTC)


class FakeMarketData:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def publish_bars(self, symbols: tuple[str, ...], **kwargs: Any) -> int:
        self.calls.append(("bars", (symbols, kwargs)))
        return 1

    async def publish_snapshots(self, symbols: tuple[str, ...]) -> int:
        self.calls.append(("snapshots", symbols))
        return 1

    async def stream_once(self, symbols: tuple[str, ...], **kwargs: Any) -> int:
        self.calls.append(("stream", (symbols, kwargs)))
        return 0


class FakeBus:
    def __init__(self) -> None:
        self.joins = 0

    async def join(self) -> None:
        self.joins += 1


class FakeRuntime:
    def __init__(self) -> None:
        self.evaluated: tuple[str, ...] = ()
        self.enabled = False

    async def evaluate_all(self, symbols: tuple[str, ...]) -> None:
        self.evaluated = symbols

    def enable_live(self) -> None:
        self.enabled = True


class FakeSec:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], datetime]] = []

    async def refresh(self, symbols: tuple[str, ...], as_of: datetime) -> None:
        self.calls.append((symbols, as_of))


@pytest.mark.unit
async def test_initialize_backfills_each_required_timeframe_before_enabling_live() -> None:
    market_data = FakeMarketData()
    bus = FakeBus()
    runtime = FakeRuntime()
    sec = FakeSec()
    service = LiveAnalysisService(
        symbols=("aapl", "MSFT", "AAPL"),
        market_data=market_data,
        local_bus=bus,
        runtime=runtime,
        sec_refresher=sec,
    )

    summary = await service.initialize(NOW)

    timeframes = tuple(
        payload[1]["timeframe"]
        for kind, payload in market_data.calls
        if kind == "bars"
    )
    assert timeframes == ("1Week", "1Day", "15Min", "1Min")
    assert market_data.calls[-1] == ("snapshots", ("AAPL", "MSFT"))
    assert bus.joins == 5
    assert sec.calls == [(("AAPL", "MSFT"), NOW)]
    assert runtime.evaluated == ("AAPL", "MSFT")
    assert runtime.enabled is True
    assert summary.symbols == ("AAPL", "MSFT")
    assert summary.market_events == 5
