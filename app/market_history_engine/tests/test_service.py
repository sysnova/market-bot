from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.contracts import (
    BarTimeframe,
    MarketBar,
    MarketHistoryRequest,
    MarketHistoryRequirement,
    MarketHistoryStatus,
)
from app.market_history_engine import BarCoverage, MarketHistoryService

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


class FakeRest:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fetch_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10_000,
    ) -> dict[str, list[dict[str, object]]]:
        self.calls.append(
            {
                "symbols": symbols,
                "timeframe": timeframe,
                "start": start,
                "end": end,
                "limit": limit,
            }
        )
        return {
            symbol: [
                {
                    "t": "2026-08-02T14:59:00Z",
                    "o": 100,
                    "h": 102,
                    "l": 99,
                    "c": 101,
                    "v": 1200,
                    "n": 30,
                    "vw": 100.5,
                }
            ]
            for symbol in symbols
        }


class FakeRepository:
    def __init__(self, coverage: dict[str, BarCoverage]) -> None:
        self._coverage = coverage
        self.saved: list[MarketBar] = []

    async def coverage(
        self, symbols: tuple[str, ...], timeframe: BarTimeframe
    ) -> dict[str, BarCoverage]:
        return {
            symbol: self._coverage.get(symbol, BarCoverage(count=0, latest=None))
            for symbol in symbols
        }

    async def upsert(self, bars: tuple[MarketBar, ...]) -> int:
        self.saved.extend(bars)
        return len(bars)


def request(timeframe: BarTimeframe, lookback: timedelta, count: int) -> MarketHistoryRequest:
    return MarketHistoryRequest(
        engine_id="test-engine",
        symbols=("TGT", "ADUR"),
        requirements=(
            MarketHistoryRequirement(
                timeframe=timeframe,
                lookback=lookback,
                max_bars_per_symbol=count,
            ),
        ),
        requested_at=NOW,
    )


async def test_initial_sync_downloads_bounded_history_and_persists_normalized_bars() -> None:
    rest = FakeRest()
    repository = FakeRepository({})
    service = MarketHistoryService(
        rest=rest,
        repository=repository,
        feed="sip",
        batch_size=20,
    )

    response = await service.ensure(request(BarTimeframe.DAY_1, timedelta(days=400), 260))

    assert response.status is MarketHistoryStatus.READY
    assert response.persisted_bars == 2
    assert rest.calls[0]["start"] == NOW - timedelta(days=400)
    assert {bar.symbol for bar in repository.saved} == {"TGT", "ADUR"}
    assert all(bar.volume == Decimal("1200") for bar in repository.saved)


async def test_incremental_sync_uses_latest_bar_with_timeframe_overlap() -> None:
    latest = NOW - timedelta(minutes=20)
    rest = FakeRest()
    repository = FakeRepository(
        {
            "TGT": BarCoverage(count=500, latest=latest),
            "ADUR": BarCoverage(count=500, latest=latest),
        }
    )
    service = MarketHistoryService(
        rest=rest,
        repository=repository,
        feed="sip",
        batch_size=20,
    )

    await service.ensure(request(BarTimeframe.MINUTE_1, timedelta(days=7), 500))

    assert rest.calls[0]["start"] == latest - timedelta(minutes=2)


async def test_fresh_cache_skips_rest_during_engine_startup() -> None:
    downloaded_at = NOW - timedelta(minutes=10)
    rest = FakeRest()
    repository = FakeRepository(
        {
            "TGT": BarCoverage(count=500, latest=NOW, downloaded_at=downloaded_at),
            "ADUR": BarCoverage(count=120, latest=NOW, downloaded_at=downloaded_at),
        }
    )
    service = MarketHistoryService(
        rest=rest,
        repository=repository,
        feed="sip",
        batch_size=20,
        freshness=timedelta(hours=1),
    )

    response = await service.ensure(request(BarTimeframe.MINUTE_1, timedelta(days=7), 500))

    assert response.persisted_bars == 0
    assert rest.calls == []


async def test_registered_requirements_are_merged_for_hourly_refresh() -> None:
    rest = FakeRest()
    repository = FakeRepository({})
    service = MarketHistoryService(
        rest=rest,
        repository=repository,
        feed="sip",
        batch_size=20,
    )
    await service.ensure(request(BarTimeframe.DAY_1, timedelta(days=220), 120))
    await service.ensure(request(BarTimeframe.DAY_1, timedelta(days=400), 260))
    rest.calls.clear()

    responses = await service.refresh_registered(as_of=NOW + timedelta(hours=1))

    assert len(responses) == 1
    assert len(rest.calls) == 1
    assert rest.calls[0]["start"] == NOW + timedelta(hours=1) - timedelta(days=400)


async def test_hourly_refresh_does_not_expand_deep_history_to_unneeded_symbols() -> None:
    rest = FakeRest()
    repository = FakeRepository({})
    service = MarketHistoryService(
        rest=rest,
        repository=repository,
        feed="sip",
        batch_size=20,
    )
    broad = MarketHistoryRequest(
        engine_id="swing-v2",
        symbols=("TGT", "ADUR"),
        requirements=(
            MarketHistoryRequirement(
                timeframe=BarTimeframe.DAY_1,
                lookback=timedelta(days=220),
                max_bars_per_symbol=120,
            ),
        ),
        requested_at=NOW,
    )
    deep = MarketHistoryRequest(
        engine_id="support-v0",
        symbols=("TGT",),
        requirements=(
            MarketHistoryRequirement(
                timeframe=BarTimeframe.DAY_1,
                lookback=timedelta(days=800),
                max_bars_per_symbol=520,
            ),
        ),
        requested_at=NOW,
    )
    await service.ensure(broad)
    await service.ensure(deep)
    rest.calls.clear()

    responses = await service.refresh_registered(as_of=NOW + timedelta(hours=1))

    assert len(responses) == 2
    calls_by_symbols = {call["symbols"]: call for call in rest.calls}
    assert calls_by_symbols[("TGT",)]["start"] == (NOW + timedelta(hours=1) - timedelta(days=800))
    assert calls_by_symbols[("ADUR",)]["start"] == (NOW + timedelta(hours=1) - timedelta(days=220))


async def test_retention_keeps_a_safety_margin_over_registered_requirements() -> None:
    service = MarketHistoryService(
        rest=FakeRest(),
        repository=FakeRepository({}),
        feed="sip",
        batch_size=20,
    )
    await service.ensure(request(BarTimeframe.MINUTE_1, timedelta(days=7), 500))

    assert service.retention_limits()[BarTimeframe.MINUTE_1] == 750
