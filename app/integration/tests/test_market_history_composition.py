from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import BarTimeframe, MarketBar, MarketHistoryRequirement
from app.integration.market_history_composition import MarketHistoryLoader

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


def bar(timeframe: BarTimeframe) -> MarketBar:
    return MarketBar(
        symbol="TGT",
        timeframe=timeframe,
        timestamp=NOW,
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
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], BarTimeframe, int]] = []

    async def load_latest(
        self,
        symbols: tuple[str, ...],
        timeframe: BarTimeframe,
        *,
        limit_per_symbol: int,
    ) -> tuple[MarketBar, ...]:
        self.calls.append((symbols, timeframe, limit_per_symbol))
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
