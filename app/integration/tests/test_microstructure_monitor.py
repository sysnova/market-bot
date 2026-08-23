from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.contracts import (
    IntradaySide,
    OrderFlowState,
    OrderFlowStateKind,
    OrderFlowWindow,
    ScalpAssessment,
    ScalpDirection,
    ScalpSetup,
    ScalpState,
    new_uuid7,
)
from app.integration.microstructure_monitor import (
    IntradayOpportunityDashboard,
    ScalpingDashboard,
    format_intraday_opportunity_dashboard,
    format_scalping_dashboard,
)
from app.intraday_opportunity_engine import (
    InMemoryIntradayOpportunityStore,
    IntradayOpportunityEngine,
)

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _window(seconds: int) -> OrderFlowWindow:
    return OrderFlowWindow(
        window_seconds=seconds,
        trade_count=20,
        buy_volume=Decimal("1200"),
        sell_volume=Decimal("600"),
        neutral_volume=Decimal("0"),
        unknown_volume=Decimal("0"),
        delta=Decimal("600"),
        volume_velocity=Decimal("180"),
        large_buy_volume=Decimal("500"),
        large_sell_volume=Decimal("100"),
        price_change_bps=Decimal("8"),
    )


def _flow() -> OrderFlowState:
    return OrderFlowState(
        symbol="AAPL",
        occurred_at=NOW,
        engine_version="1.0.0",
        state=OrderFlowStateKind.SELLER_EXHAUSTION,
        current_price=Decimal("100.50"),
        mid_price=Decimal("100.50"),
        cumulative_delta=Decimal("2400"),
        confidence=Decimal("0.82"),
        data_quality=Decimal("0.92"),
        quote_age_ms=Decimal("100"),
        quote_fresh=True,
        unknown_trade_ratio=Decimal("0"),
        windows=tuple(_window(seconds) for seconds in (1, 5, 15, 60, 300)),
        reasons=("seller_exhaustion",),
        context_hash="sha256:" + "b" * 64,
    )


def _scalp() -> ScalpAssessment:
    return ScalpAssessment(
        symbol="AAPL",
        occurred_at=NOW,
        engine_version="1.0.0",
        state=ScalpState.ARMED,
        setup=ScalpSetup.SUPPORT_REVERSAL,
        direction=ScalpDirection.LONG,
        current_price=Decimal("100.50"),
        bid_price=Decimal("100.49"),
        ask_price=Decimal("100.51"),
        session_vwap=Decimal("101"),
        spread_bps=Decimal("2"),
        order_flow_confidence=Decimal("0.82"),
        entry_price=Decimal("100.50"),
        invalidation=Decimal("99.50"),
        target=Decimal("102"),
        max_hold_seconds=600,
        support_low=Decimal("100"),
        support_high=Decimal("100.80"),
        source_order_flow_state_id=_flow().state_id,
        reasons=("support_reversal_armed",),
        context_hash="sha256:" + "c" * 64,
    )


def test_scalping_dashboard_combines_flow_and_setup_levels() -> None:
    dashboard = ScalpingDashboard(history=40)
    dashboard.merge_flow(_flow())
    dashboard.merge_scalp(_scalp())

    rendered = format_scalping_dashboard(dashboard, refreshed_at=NOW)

    assert "SCALPING | PAPER" in rendered
    assert "AAPL" in rendered
    assert "SELLER_EXHAUSTION" in rendered
    assert "SUPPORT_REVERSAL" in rendered
    assert "ENTRY 100.5000" in rendered
    assert "STOP 99.5000" in rendered
    assert "TARGET 102.0000" in rendered
    assert "D5 +600.0000" in rendered


@pytest.mark.unit
async def test_intraday_dashboard_shows_live_pnl_and_weekly_effectiveness() -> None:
    store = InMemoryIntradayOpportunityStore()
    engine = IntradayOpportunityEngine(store=store)
    opened = await engine.open_position(
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
    assert opened is not None
    marked = await engine.mark_quote(
        source_event_id=new_uuid7(),
        symbol="AAPL",
        strategy_id="SCALP-V1",
        bid=Decimal("101"),
        ask=Decimal("101.10"),
        occurred_at=NOW + timedelta(minutes=1),
    )
    assert marked is not None
    dashboard = IntradayOpportunityDashboard(history=50)
    dashboard.merge(marked.opportunity)

    rendered = format_intraday_opportunity_dashboard(
        dashboard,
        refreshed_at=NOW + timedelta(minutes=1),
        days=7,
    )

    assert "INTRADAY OPS | PAPER" in rendered
    assert "OPEN 1" in rendered
    assert "AAPL LONG OPEN" in rendered
    assert "ENTRY 100.1000" in rendered
    assert "MARK 101.0000" in rendered
    assert "P/L +" in rendered
