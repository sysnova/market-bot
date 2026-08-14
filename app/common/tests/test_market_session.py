from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.common.market_session import (
    is_completed_daily_bar,
    is_regular_analytical_bar,
    market_session,
)
from app.contracts import BarTimeframe, MarketBar, MarketSession


def _bar(timeframe: BarTimeframe, timestamp: datetime) -> MarketBar:
    return MarketBar(
        symbol="AAPL",
        timeframe=timeframe,
        timestamp=timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1000"),
        source="test",
        feed="sip",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("timestamp", "expected"),
    (
        (datetime(2026, 7, 24, 13, 29, tzinfo=UTC), MarketSession.PRE_MARKET),
        (datetime(2026, 7, 24, 13, 30, tzinfo=UTC), MarketSession.REGULAR),
        (datetime(2026, 7, 24, 19, 59, tzinfo=UTC), MarketSession.REGULAR),
        (datetime(2026, 7, 24, 20, 0, tzinfo=UTC), MarketSession.AFTER_HOURS),
    ),
)
def test_market_session_uses_new_york_rth(timestamp: datetime, expected: MarketSession) -> None:
    assert market_session(timestamp) is expected


@pytest.mark.unit
def test_only_intraday_bars_are_session_gated() -> None:
    premarket = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)

    assert not is_regular_analytical_bar(_bar(BarTimeframe.MINUTE_1, premarket))
    assert is_regular_analytical_bar(_bar(BarTimeframe.DAY_1, premarket))


@pytest.mark.unit
def test_current_daily_bar_is_not_structurally_complete() -> None:
    daily = _bar(BarTimeframe.DAY_1, datetime(2026, 7, 24, 4, 0, tzinfo=UTC))

    assert not is_completed_daily_bar(daily, as_of=datetime(2026, 7, 24, 21, 0, tzinfo=UTC))
    assert is_completed_daily_bar(daily, as_of=datetime(2026, 7, 27, 13, 0, tzinfo=UTC))
