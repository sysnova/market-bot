from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.contracts import BarTimeframe, MarketBar
from app.integration.market_bar_repository import PostgresMarketBarRepository

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeConnection:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = results
        self.calls: list[tuple[Any, Any]] = []

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: Any, parameters: Any = None) -> FakeResult:
        self.calls.append((statement, parameters))
        return self.results.pop(0) if self.results else FakeResult([])

    async def scalar(self, statement: Any, parameters: Any = None) -> int:
        self.calls.append((statement, parameters))
        return 12


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def connect(self) -> FakeConnection:
        return self.connection

    def begin(self) -> FakeConnection:
        return self.connection


def bar(symbol: str = "TGT") -> MarketBar:
    return MarketBar(
        symbol=symbol,
        timeframe=BarTimeframe.MINUTE_1,
        timestamp=NOW,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("1200"),
        trade_count=30,
        vwap=Decimal("100.5"),
        source="alpaca",
        feed="sip",
        is_final=True,
    )


async def test_repository_upserts_bars_in_one_database_transaction() -> None:
    connection = FakeConnection([])
    repository = PostgresMarketBarRepository(FakeEngine(connection))  # type: ignore[arg-type]

    persisted = await repository.upsert((bar(), bar("ADUR")))

    assert persisted == 2
    assert len(connection.calls) == 1
    statement, values = connection.calls[0]
    assert "ON CONFLICT" in str(statement.compile()).upper()
    assert [value["symbol"] for value in values] == ["TGT", "ADUR"]


async def test_repository_reports_coverage_and_loads_latest_market_bars() -> None:
    rows = [SimpleNamespace(symbol="TGT", count=500, latest=NOW)]
    loaded = [
        SimpleNamespace(
            symbol="TGT",
            timeframe="1Min",
            timestamp=NOW,
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=Decimal("1200"),
            trade_count=30,
            vwap=Decimal("100.5"),
            source="alpaca",
            feed="sip",
            is_final=True,
        )
    ]
    connection = FakeConnection([FakeResult(rows), FakeResult(loaded)])
    repository = PostgresMarketBarRepository(FakeEngine(connection))  # type: ignore[arg-type]

    coverage = await repository.coverage(("TGT", "ADUR"), BarTimeframe.MINUTE_1)
    bars = await repository.load_latest(
        ("TGT", "ADUR"), BarTimeframe.MINUTE_1, limit_per_symbol=500
    )

    assert coverage["TGT"].count == 500
    assert coverage["ADUR"].count == 0
    assert bars == (bar(),)


async def test_repository_prunes_through_bounded_database_function() -> None:
    connection = FakeConnection([])
    repository = PostgresMarketBarRepository(FakeEngine(connection))  # type: ignore[arg-type]

    removed = await repository.prune(BarTimeframe.MINUTE_1, keep_per_symbol=750)

    assert removed == 12
    _, parameters = connection.calls[0]
    assert parameters == {"timeframe": "1Min", "keep_per_symbol": 750}
