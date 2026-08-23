from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.contracts.intraday_opportunity import (
    IntradayCloseReason,
    IntradayFill,
    IntradayFillRole,
    IntradayOpportunity,
    IntradayOpportunityStatus,
    IntradaySide,
    IntradayTradeAction,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _open_opportunity() -> IntradayOpportunity:
    fill = IntradayFill(
        fill_id=UUID("0195f3a5-9000-7000-8000-000000000101"),
        opportunity_id=UUID("0195f3a5-9000-7000-8000-000000000100"),
        source_event_id=UUID("0195f3a5-9000-7000-8000-000000000102"),
        occurred_at=NOW,
        role=IntradayFillRole.ENTRY,
        action=IntradayTradeAction.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.10"),
        fee=Decimal("0.50"),
    )
    return IntradayOpportunity(
        opportunity_id=fill.opportunity_id,
        symbol="AAPL",
        strategy_id="support-reversal-v1",
        session_date=NOW.date(),
        side=IntradaySide.LONG,
        status=IntradayOpportunityStatus.OPEN,
        opened_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        quantity=Decimal("10"),
        entry_price=Decimal("100.10"),
        current_price=Decimal("100.00"),
        stop_price=Decimal("99.50"),
        target_price=Decimal("101.50"),
        highest_mark=Decimal("100.00"),
        lowest_mark=Decimal("100.00"),
        gross_pnl=Decimal("-1.00"),
        net_pnl=Decimal("-1.50"),
        gross_pnl_percent=Decimal("-0.0999"),
        net_pnl_percent=Decimal("-0.1499"),
        mfe_percent=Decimal("0"),
        mae_percent=Decimal("-0.0999"),
        fees_total=Decimal("0.50"),
        source_signal_id=fill.source_event_id,
        entry_fill=fill,
    )


@pytest.mark.unit
def test_intraday_opportunity_accepts_open_snapshot() -> None:
    opportunity = _open_opportunity()

    assert opportunity.status is IntradayOpportunityStatus.OPEN
    assert opportunity.entry_fill.action is IntradayTradeAction.BUY


@pytest.mark.unit
def test_intraday_opportunity_requires_utc_timestamps() -> None:
    payload = _open_opportunity().model_dump(mode="python")
    payload["updated_at"] = datetime(2026, 8, 24, 14, 31)

    with pytest.raises(ValueError, match="timezone-aware UTC"):
        IntradayOpportunity.model_validate(payload)


@pytest.mark.unit
def test_closed_intraday_opportunity_requires_exit_evidence() -> None:
    payload = _open_opportunity().model_dump(mode="python")
    payload["status"] = IntradayOpportunityStatus.CLOSED

    with pytest.raises(ValueError, match="closed opportunity requires exit evidence"):
        IntradayOpportunity.model_validate(payload)


@pytest.mark.unit
def test_intraday_levels_follow_side_direction() -> None:
    payload = _open_opportunity().model_dump(mode="python")
    payload["stop_price"] = Decimal("101")

    with pytest.raises(ValueError, match="LONG levels"):
        IntradayOpportunity.model_validate(payload)

    payload = _open_opportunity().model_dump(mode="python")
    payload["side"] = IntradaySide.SHORT
    payload["stop_price"] = Decimal("101")
    payload["target_price"] = Decimal("99")
    payload["entry_fill"]["action"] = IntradayTradeAction.SELL

    assert IntradayOpportunity.model_validate(payload).side is IntradaySide.SHORT


@pytest.mark.unit
def test_close_reason_values_cover_intraday_risk_controls() -> None:
    assert {
        IntradayCloseReason.STOP,
        IntradayCloseReason.TARGET,
        IntradayCloseReason.TIME_EXIT,
        IntradayCloseReason.END_OF_DAY,
        IntradayCloseReason.FLOW_REVERSAL,
        IntradayCloseReason.MANUAL,
    } == set(IntradayCloseReason)
