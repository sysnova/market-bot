from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.contracts import (
    MARKET_HISTORY_ENSURE_SUBJECT,
    BarTimeframe,
    MarketHistoryRequest,
    MarketHistoryRequirement,
    MarketHistoryResponse,
    MarketHistoryStatus,
)

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


def test_market_history_contract_is_small_and_versioned() -> None:
    request = MarketHistoryRequest(
        engine_id="swing-v2",
        symbols=("tgt", "ADUR"),
        requirements=(
            MarketHistoryRequirement(
                timeframe=BarTimeframe.DAY_1,
                lookback=timedelta(days=220),
                max_bars_per_symbol=120,
            ),
        ),
        requested_at=NOW,
    )
    response = MarketHistoryResponse(
        request_id=request.request_id,
        status=MarketHistoryStatus.READY,
        synced_through=NOW,
        persisted_bars=42,
    )

    assert MARKET_HISTORY_ENSURE_SUBJECT == "marketbot.rpc.v1.market.history.ensure"
    assert request.symbols == ("TGT", "ADUR")
    assert response.error is None


def test_market_history_request_rejects_duplicate_timeframes() -> None:
    requirement = MarketHistoryRequirement(
        timeframe=BarTimeframe.MINUTE_1,
        lookback=timedelta(days=7),
        max_bars_per_symbol=500,
    )

    with pytest.raises(ValidationError, match="timeframes"):
        MarketHistoryRequest(
            engine_id="intraday-v2",
            symbols=("TGT",),
            requirements=(requirement, requirement),
            requested_at=NOW,
        )


def test_error_response_requires_a_message() -> None:
    with pytest.raises(ValidationError, match="error"):
        MarketHistoryResponse(
            status=MarketHistoryStatus.ERROR,
            synced_through=NOW,
            persisted_bars=0,
        )
