from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts import BarTimeframe
from app.intraday_engine.models import IntradayContext

from .helpers import trend_bars


def test_context_rejects_non_minute_timeframes() -> None:
    bars = trend_bars(
        symbol="AAPL",
        start=Decimal("100"),
        step=Decimal("1"),
        final_move=Decimal("1"),
        base_volume=Decimal("1000"),
        final_volume=Decimal("1000"),
        timeframe=BarTimeframe.DAY_1,
    )

    with pytest.raises(ValidationError, match="1Min"):
        IntradayContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp,
            minute_bars=bars,
        )


def test_context_rejects_future_or_out_of_order_bars() -> None:
    bars = trend_bars(
        symbol="AAPL",
        start=Decimal("100"),
        step=Decimal("0.1"),
        final_move=Decimal("0.1"),
        base_volume=Decimal("1000"),
        final_volume=Decimal("1000"),
    )

    with pytest.raises(ValidationError, match="chronological"):
        IntradayContext(
            symbol="AAPL",
            as_of=datetime(2026, 7, 24, 20, tzinfo=UTC),
            minute_bars=(bars[1], bars[0]),
        )

    with pytest.raises(ValidationError, match="later than as_of"):
        IntradayContext(
            symbol="AAPL",
            as_of=bars[-1].timestamp - timedelta(minutes=1),
            minute_bars=bars,
        )
