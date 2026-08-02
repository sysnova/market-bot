from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integration.market_rotation_store import PostgresMarketRotationStore
from app.market_rotation_engine import RotationResult


@pytest.mark.unit
async def test_rotation_upsert_merges_existing_watchlist_metadata() -> None:
    customer = MagicMock()
    customer.scalar_one.return_value = "customer-1"
    connection = AsyncMock()
    connection.execute.side_effect = [
        customer,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    ]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    database = MagicMock()
    database.begin.return_value = context
    evidence = {
        "symbol": "TEST",
        "price": Decimal("100"),
        "return_1d": Decimal("1"),
        "return_5d": Decimal("2"),
        "return_20d": Decimal("3"),
        "dollar_volume": Decimal("1000000"),
        "average_dollar_volume_20": Decimal("900000"),
        "rvol": Decimal("1.1"),
        "above_sma20": True,
        "above_sma50": True,
        "score": Decimal("75"),
    }
    result: RotationResult = {
        "code": "TECH",
        "label": "Technology",
        "proxy": "XLK",
        "benchmark": "SPY",
        "score": Decimal("75"),
        "state": "INFLOW",
        "relative_20d": Decimal("3"),
        "breadth_positive": Decimal("70"),
        "breadth_above_sma20": Decimal("70"),
        "rvol": Decimal("1.1"),
        "evidence": (evidence,),
    }

    _, additions = await PostgresMarketRotationStore(database).save(
        (result,), generated_at=datetime(2026, 8, 2, 15, tzinfo=UTC)
    )

    assert additions == ("TEST",)
    upsert = str(connection.execute.await_args_list[-1].args[0])
    normalized = " ".join(upsert.split())
    assert "insert into stock.watchlist_symbol as existing" in normalized
    assert (
        "metadata_json=coalesce(existing.metadata_json,'{}'::jsonb) "
        "|| excluded.metadata_json"
    ) in normalized
