from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.integration.postgres_universe import (
    PostgresUniverseClient,
    PostgresUniverseError,
)


def _engine_with_rows(*result_rows: list[SimpleNamespace]) -> MagicMock:
    results = []
    for rows in result_rows:
        result = MagicMock()
        result.all.return_value = rows
        results.append(result)
    connection = AsyncMock()
    connection.execute.side_effect = results
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = context
    return engine


@pytest.mark.unit
async def test_universe_merges_dynamic_local_watchlist_and_holdings() -> None:
    watchlist_time = datetime(2026, 7, 28, 12, tzinfo=UTC)
    holdings_time = datetime(2026, 7, 28, 13, tzinfo=UTC)
    engine = _engine_with_rows(
        [
            SimpleNamespace(symbol="asts", updated_at=watchlist_time),
            SimpleNamespace(symbol="IREN", updated_at=watchlist_time),
        ],
        [SimpleNamespace(symbol="nbis", updated_at=holdings_time)],
    )

    universe = await PostgresUniverseClient(engine).get_universe()

    assert universe.symbols == ("ASTS", "IREN", "NBIS")
    assert universe.source == "postgresql-local"
    assert universe.watchlist_updated_at == watchlist_time.isoformat()
    assert universe.holdings_updated_at == holdings_time.isoformat()


@pytest.mark.unit
async def test_universe_wraps_local_database_errors() -> None:
    engine = _engine_with_rows([], [])
    connection = await engine.connect.return_value.__aenter__()
    connection.execute.side_effect = OperationalError("query", {}, RuntimeError("offline"))

    with pytest.raises(PostgresUniverseError, match="Local PostgreSQL"):
        await PostgresUniverseClient(engine).get_universe()


@pytest.mark.unit
async def test_portfolio_allocations_are_loaded_from_tagged_watchlist_metadata() -> None:
    engine = _engine_with_rows([
        SimpleNamespace(symbol="hims", target_weight_percent="11.73"),
        SimpleNamespace(symbol="NVO", target_weight_percent="4.31"),
    ])

    allocations = await PostgresUniverseClient(engine).get_portfolio_allocations()

    assert [(item.symbol, str(item.weight_percent)) for item in allocations] == [
        ("HIMS", "11.73"),
        ("NVO", "4.31"),
    ]


@pytest.mark.unit
async def test_portfolio_allocations_require_at_least_one_tagged_symbol() -> None:
    with pytest.raises(PostgresUniverseError, match="no active PORT_YTD"):
        await PostgresUniverseClient(_engine_with_rows([])).get_portfolio_allocations()


@pytest.mark.unit
async def test_holding_quantity_uses_authoritative_customer_holding() -> None:
    connection = AsyncMock()
    connection.scalar.return_value = Decimal("28")
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = context

    quantity = await PostgresUniverseClient(engine).get_holding_quantity("hims")

    assert quantity == Decimal("28")
    assert connection.scalar.await_args.args[1]["symbol"] == "HIMS"
