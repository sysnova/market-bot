from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis
import pytest

from app.contracts import BarTimeframe, MarketBar, MarketHistoryRequirement
from app.integration.redis_history import RedisHistoryBars, RedisHistoryWarmer
from app.integration.redis_ticker_cache import RedisTickerCache
from app.integration.tests.test_long_term_worker import (
    test_long_worker_owns_history_and_accepts_explicit_final_daily_bar,
    test_long_worker_reprices_completed_history_from_live_minutes,
)
from app.integration.tests.test_swing_trade_composition import (
    test_momentum_history_bootstraps_four_hour_and_rolls_daily_after_close,
    test_observations_refresh_without_duplicate_signals_or_transitions,
)
from app.market_history_engine import BarCoverage


class Repository:
    def __init__(self) -> None:
        self.reads: list[tuple[str, ...]] = []
        self.version = datetime(2026, 9, 15, tzinfo=UTC)

    async def coverage(
        self, symbols: tuple[str, ...], timeframe: BarTimeframe
    ) -> dict[str, BarCoverage]:
        return {s: BarCoverage(1, self.version, self.version) for s in symbols}

    async def load_latest(
        self, symbols: tuple[str, ...], timeframe: BarTimeframe, **kwargs: object
    ) -> tuple[MarketBar, ...]:
        self.reads.append(symbols)
        return tuple(
            MarketBar(
                symbol=s,
                timeframe=timeframe,
                timestamp=self.version - timedelta(days=1),
                open=Decimal(10),
                high=Decimal(11),
                low=Decimal(9),
                close=Decimal(10),
                volume=Decimal(100),
                source="alpaca",
                feed="sip",
                is_final=True,
            )
            for s in symbols
        )


async def test_load_once_then_restart_reads_redis_and_invalidates_changed_coverage() -> None:
    server = fakeredis.FakeServer()
    cache = RedisTickerCache(fakeredis.FakeRedis(server=server, decode_responses=True))
    repo = Repository()
    requirement = MarketHistoryRequirement(
        timeframe=BarTimeframe.DAY_1, max_bars_per_symbol=10, lookback=timedelta(days=30)
    )
    warmer = RedisHistoryWarmer(cache, repo)
    await warmer.warm(("AAPL", "MSFT"), (requirement,))
    assert repo.reads == [("AAPL",), ("MSFT",)]
    cache.close()
    restarted = RedisTickerCache(fakeredis.FakeRedis(server=server, decode_responses=True))
    await RedisHistoryWarmer(restarted, repo).warm(("AAPL", "MSFT"), (requirement,))
    assert len(repo.reads) == 2
    bars = RedisHistoryBars(restarted, ("AAPL", "MSFT"), (requirement,), repo.version, False)
    assert [b.symbol for b in bars] == ["AAPL", "MSFT"]
    assert len(bars) == 2
    repo.version += timedelta(days=1)
    await RedisHistoryWarmer(restarted, repo).warm(("AAPL", "MSFT"), (requirement,))
    assert len(repo.reads) == 4


@pytest.mark.parametrize(
    "case",
    [
        test_long_worker_owns_history_and_accepts_explicit_final_daily_bar,
        test_long_worker_reprices_completed_history_from_live_minutes,
        test_momentum_history_bootstraps_four_hour_and_rolls_daily_after_close,
        test_observations_refresh_without_duplicate_signals_or_transitions,
    ],
)
async def test_engine_results_match_with_redis(
    monkeypatch: pytest.MonkeyPatch,
    case: Callable[[], Awaitable[None]],
) -> None:
    from app.common import context_cache
    from app.integration import ticker_cache_transport

    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    monkeypatch.setattr(context_cache, "_backend", cache)
    monkeypatch.setattr(ticker_cache_transport, "_client", cache)
    try:
        await case()
    finally:
        cache.close()


async def test_lazy_reader_filters_forming_bars_and_sorts_only_one_ticker() -> None:
    from app.integration.redis_history import bar_row, chronological_bars, history_view
    from app.integration.tests.test_market_bar_store import bar

    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    a, b = bar(minute=0, close="100"), bar(minute=1, close="101")
    a = a.model_copy(update={"timestamp": a.timestamp + timedelta(days=1)})
    b = b.model_copy(update={"timestamp": b.timestamp + timedelta(days=1)})
    req = MarketHistoryRequirement(
        timeframe=BarTimeframe.MINUTE_1,
        lookback=timedelta(days=1),
        max_bars_per_symbol=2,
    )
    view = history_view("AAPL", BarTimeframe.MINUTE_1, True)
    cache.open_persistent(view, 2)
    cache.call("add", view, [bar_row(b), bar_row(a)])
    lazy = RedisHistoryBars(cache, ("AAPL",), (req,), b.timestamp, False)
    assert tuple(lazy) == (a,)
    assert tuple(chronological_bars(lazy)) == (a,)
    assert len(lazy) == 1


async def test_distributed_loader_uses_redis_without_opening_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.common.settings import AppSettings
    from app.contracts import MarketHistoryRequest, MarketHistoryResponse, MarketHistoryStatus
    from app.integration import market_history_composition, ticker_cache_transport

    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    repo = Repository()
    warmer = RedisHistoryWarmer(cache, repo)

    class CentralClient:
        closed = False

        async def ensure(self, request: MarketHistoryRequest) -> MarketHistoryResponse:
            await warmer.warm(request.symbols, request.requirements)
            return MarketHistoryResponse(
                request_id=request.request_id,
                status=MarketHistoryStatus.READY,
                synced_through=request.requested_at,
                persisted_bars=0,
            )

        async def close(self) -> None:
            self.closed = True

    central = CentralClient()

    async def connect(*args: object, **kwargs: object) -> CentralClient:
        return central

    def forbidden_repository(*args: object, **kwargs: object) -> None:
        pytest.fail("engine must not read a PostgreSQL history window")

    monkeypatch.setattr(ticker_cache_transport, "_client", cache)
    monkeypatch.setattr(market_history_composition.NatsMarketHistoryClient, "connect", connect)
    monkeypatch.setattr(
        market_history_composition, "PostgresMarketBarRepository", forbidden_repository
    )
    req = MarketHistoryRequirement(
        timeframe=BarTimeframe.DAY_1,
        lookback=timedelta(days=30),
        max_bars_per_symbol=10,
    )
    result = await market_history_composition.load_market_history_profiled(
        AppSettings(_env_file=None),
        None,
        engine_id="test",
        symbols=("AAPL",),
        requirements=(req,),
        as_of=repo.version,
    )
    assert isinstance(result.bars, RedisHistoryBars)
    assert central.closed
    assert [bar.symbol for bar in result.bars] == ["AAPL"]


async def test_stream_updates_redis_before_notifying_consumers() -> None:
    from app.contracts import MARKET_BAR_EVENT, EventEnvelope
    from app.integration.redis_history import RedisIngressPublisher, history_view
    from app.integration.tests.test_market_bar_store import bar

    cache = RedisTickerCache(fakeredis.FakeRedis(decode_responses=True))
    value = bar(minute=0, close="100")
    view = history_view("AAPL", BarTimeframe.MINUTE_1, False)
    cache.open_persistent(view, 2)

    class Publisher:
        published = False

        async def publish(self, subject: str, envelope: EventEnvelope) -> None:
            assert cache.call("history", view, "AAPL", "1Min", 1, True) == [value.model_dump_json()]
            self.published = True

    target = Publisher()
    ingress = RedisIngressPublisher(target, RedisHistoryWarmer(cache, Repository()))
    await ingress.publish(
        "test",
        EventEnvelope(
            event_type=MARKET_BAR_EVENT,
            occurred_at=value.timestamp,
            source="test",
            subject="AAPL",
            payload=value,
        ),
    )
    assert target.published
