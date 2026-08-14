from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar, MarketHistoryRequirement
from app.integration.market_history_composition import MarketHistoryLoader

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


def bar(timeframe: BarTimeframe, *, timestamp: datetime | None = None) -> MarketBar:
    return MarketBar(
        symbol="TGT",
        timeframe=timeframe,
        timestamp=timestamp
        or (NOW - timedelta(days=1) if timeframe is BarTimeframe.DAY_1 else NOW),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1200"),
        source="alpaca",
        feed="sip",
    )


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def ensure(self, request: object) -> object:
        self.requests.append(request)
        return object()


class FakeRepository:
    def __init__(self, bars: tuple[MarketBar, ...] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], BarTimeframe, int]] = []
        self.bars = bars

    async def load_latest(
        self,
        symbols: tuple[str, ...],
        timeframe: BarTimeframe,
        *,
        limit_per_symbol: int,
    ) -> tuple[MarketBar, ...]:
        self.calls.append((symbols, timeframe, limit_per_symbol))
        if self.bars is not None:
            return tuple(item for item in self.bars if item.timeframe is timeframe)
        return (bar(timeframe),)


async def test_loader_requests_sync_then_reads_each_required_timeframe() -> None:
    client = FakeClient()
    repository = FakeRepository()
    loader = MarketHistoryLoader(client=client, repository=repository)  # type: ignore[arg-type]
    requirements = (
        MarketHistoryRequirement(
            timeframe=BarTimeframe.DAY_1,
            lookback=timedelta(days=400),
            max_bars_per_symbol=260,
        ),
        MarketHistoryRequirement(
            timeframe=BarTimeframe.WEEK_1,
            lookback=timedelta(days=365 * 5),
            max_bars_per_symbol=220,
        ),
    )

    bars = await loader.ensure_and_load(
        engine_id="long-term-v2",
        symbols=("TGT",),
        requirements=requirements,
        as_of=NOW,
    )

    assert len(client.requests) == 1
    assert client.requests[0].engine_id == "long-term-v2"  # type: ignore[attr-defined]
    assert repository.calls == [
        (("TGT",), BarTimeframe.DAY_1, 260),
        (("TGT",), BarTimeframe.WEEK_1, 220),
    ]
    assert [item.timeframe for item in bars] == [
        BarTimeframe.DAY_1,
        BarTimeframe.WEEK_1,
    ]


async def test_loader_overfetches_and_removes_extended_intraday_bars() -> None:
    client = FakeClient()
    repository = FakeRepository(
        (
            bar(BarTimeframe.MINUTE_1, timestamp=datetime(2026, 7, 31, 12, 0, tzinfo=UTC)),
            bar(BarTimeframe.MINUTE_1, timestamp=datetime(2026, 7, 31, 14, 0, tzinfo=UTC)),
            bar(BarTimeframe.MINUTE_1, timestamp=datetime(2026, 7, 31, 14, 1, tzinfo=UTC)),
        )
    )
    loader = MarketHistoryLoader(client=client, repository=repository)  # type: ignore[arg-type]
    requirements = (
        MarketHistoryRequirement(
            timeframe=BarTimeframe.MINUTE_1,
            lookback=timedelta(days=1),
            max_bars_per_symbol=1,
        ),
    )

    bars = await loader.ensure_and_load(
        engine_id="intraday-v2",
        symbols=("TGT",),
        requirements=requirements,
        as_of=NOW,
    )

    assert repository.calls == [(("TGT",), BarTimeframe.MINUTE_1, 3)]
    assert [item.timestamp for item in bars] == [datetime(2026, 7, 31, 14, 1, tzinfo=UTC)]


async def test_loader_excludes_current_partial_daily_bar() -> None:
    client = FakeClient()
    repository = FakeRepository(
        (
            bar(BarTimeframe.DAY_1, timestamp=NOW - timedelta(days=1)),
            bar(BarTimeframe.DAY_1, timestamp=NOW),
        )
    )
    loader = MarketHistoryLoader(client=client, repository=repository)  # type: ignore[arg-type]
    requirements = (
        MarketHistoryRequirement(
            timeframe=BarTimeframe.DAY_1,
            lookback=timedelta(days=10),
            max_bars_per_symbol=10,
        ),
    )

    bars = await loader.ensure_and_load(
        engine_id="long-term-v2",
        symbols=("TGT",),
        requirements=requirements,
        as_of=NOW,
    )

    assert [item.timestamp for item in bars] == [NOW - timedelta(days=1)]
