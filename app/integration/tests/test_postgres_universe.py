from datetime import UTC, datetime
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
