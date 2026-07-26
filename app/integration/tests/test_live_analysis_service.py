import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from app.integration.live_analysis_service import LiveAnalysisService
from app.integration.supabase_universe import UniverseSnapshot

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

    def disable_live(self) -> None:
        self.enabled = False


class FakeSec:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], datetime]] = []

    async def refresh(self, symbols: tuple[str, ...], as_of: datetime) -> None:
        self.calls.append((symbols, as_of))


class BlockingMarketData(FakeMarketData):
    async def stream_once(self, symbols: tuple[str, ...], **kwargs: Any) -> int:
        self.calls.append(("stream", (symbols, kwargs)))
        await asyncio.Event().wait()
        return 0


class FakeUniverseProvider:
    async def get_universe(self) -> UniverseSnapshot:
        return UniverseSnapshot(symbols=("MSFT", "NVDA"), source="supabase")


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


@pytest.mark.unit
async def test_refresh_universe_backfills_only_added_symbols_and_reenables_live() -> None:
    market_data = FakeMarketData()
    bus = FakeBus()
    runtime = FakeRuntime()
    sec = FakeSec()
    service = LiveAnalysisService(
        symbols=("AAPL", "MSFT"),
        market_data=market_data,
        local_bus=bus,
        runtime=runtime,
        sec_refresher=sec,
    )
    runtime.enable_live()

    changed = await service.refresh_universe(("MSFT", "NVDA"), NOW)

    assert changed is True
    assert service.symbols == ("MSFT", "NVDA")
    assert all(payload[0] == ("NVDA",) for kind, payload in market_data.calls if kind == "bars")
    assert market_data.calls[-1] == ("snapshots", ("NVDA",))
    assert sec.calls == [(("NVDA",), NOW)]
    assert runtime.evaluated == ("MSFT", "NVDA")
    assert runtime.enabled is True


@pytest.mark.unit
async def test_stream_session_reconnects_when_shared_universe_changes() -> None:
    market_data = BlockingMarketData()
    runtime = FakeRuntime()
    service = LiveAnalysisService(
        symbols=("AAPL", "MSFT"),
        market_data=market_data,
        local_bus=FakeBus(),
        runtime=runtime,
    )
    stream_task = asyncio.create_task(market_data.stream_once(service.symbols))

    count, changed = await service._run_stream_session(
        stream_task,
        universe_provider=FakeUniverseProvider(),
        universe_refresh_seconds=0.001,
    )

    assert (count, changed) == (0, True)
    assert stream_task.cancelled()
    assert service.symbols == ("MSFT", "NVDA")
    assert runtime.enabled is True
