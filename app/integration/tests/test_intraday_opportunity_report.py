from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import IntradayCloseReason, IntradaySide, new_uuid7
from app.integration.intraday_opportunity_report import (
    summarize_intraday_opportunities,
)
from app.intraday_opportunity_engine import (
    InMemoryIntradayOpportunityStore,
    IntradayOpportunityEngine,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


@pytest.mark.unit
async def test_weekly_report_counts_only_closed_trades_as_effectiveness() -> None:
    store = InMemoryIntradayOpportunityStore()
    engine = IntradayOpportunityEngine(store=store)
    won = await engine.open_position(
        source_event_id=new_uuid7(),
        symbol="AAPL",
        strategy_id="SCALP-V1",
        side=IntradaySide.LONG,
        quantity=Decimal("10"),
        bid=Decimal("100"),
        ask=Decimal("100.10"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=NOW,
        max_holding=timedelta(minutes=15),
    )
    assert won is not None
    await engine.close_position(
        source_event_id=new_uuid7(),
        symbol="AAPL",
        strategy_id="SCALP-V1",
        bid=Decimal("101"),
        ask=Decimal("101.10"),
        occurred_at=NOW + timedelta(minutes=1),
        reason=IntradayCloseReason.TARGET,
    )

    lost = await engine.open_position(
        source_event_id=new_uuid7(),
        symbol="MSFT",
        strategy_id="SCALP-V1",
        side=IntradaySide.SHORT,
        quantity=Decimal("10"),
        bid=Decimal("100"),
        ask=Decimal("100.10"),
        stop_price=Decimal("101"),
        target_price=Decimal("98"),
        occurred_at=NOW + timedelta(minutes=2),
        max_holding=timedelta(minutes=15),
    )
    assert lost is not None
    await engine.close_position(
        source_event_id=new_uuid7(),
        symbol="MSFT",
        strategy_id="SCALP-V1",
        bid=Decimal("100.40"),
        ask=Decimal("100.50"),
        occurred_at=NOW + timedelta(minutes=3),
        reason=IntradayCloseReason.STOP,
    )

    opened = await engine.open_position(
        source_event_id=new_uuid7(),
        symbol="NVDA",
        strategy_id="SCALP-V1",
        side=IntradaySide.LONG,
        quantity=Decimal("5"),
        bid=Decimal("50"),
        ask=Decimal("50.10"),
        stop_price=Decimal("49"),
        target_price=Decimal("52"),
        occurred_at=NOW + timedelta(minutes=4),
        max_holding=timedelta(minutes=15),
    )
    assert opened is not None

    report = summarize_intraday_opportunities(
        store.opportunities.values(),
        start_date=NOW.date(),
        end_date=NOW.date(),
    )

    assert report["total_opportunities"] == 3
    assert report["closed"] == 2
    assert report["open"] == 1
    assert report["wins"] == 1
    assert report["losses"] == 1
    assert report["effectiveness_rate_percent"] == "50.0000"
    assert report["expectancy_net_percent"] is not None
    assert len(report["operations"]) == 3


def test_empty_report_does_not_invent_effectiveness() -> None:
    report = summarize_intraday_opportunities(
        (), start_date=NOW.date(), end_date=NOW.date()
    )

    assert report["closed"] == 0
    assert report["effectiveness_rate_percent"] is None
    assert report["expectancy_net_percent"] is None
    assert report["profit_factor"] is None
