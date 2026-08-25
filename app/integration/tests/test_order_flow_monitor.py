from datetime import UTC, datetime
from decimal import Decimal

from app.contracts import OrderFlowState, OrderFlowStateKind, OrderFlowWindow
from app.integration.order_flow_monitor import (
    OrderFlowDashboard,
    format_order_flow_dashboard,
    order_flow_monitor_subjects,
)

NOW = datetime(2026, 8, 25, 15, tzinfo=UTC)
SYMBOLS = ("ASTS", "ASTX", "ASTN", "NBIS", "NBIZ")


def test_order_flow_monitor_uses_only_exact_bounded_state_subjects() -> None:
    assert order_flow_monitor_subjects(SYMBOLS) == (
        "marketbot.v1.order-flow.state.ASTS",
        "marketbot.v1.order-flow.state.ASTX",
        "marketbot.v1.order-flow.state.ASTN",
        "marketbot.v1.order-flow.state.NBIS",
        "marketbot.v1.order-flow.state.NBIZ",
    )


def test_order_flow_dashboard_renders_states_and_pending_symbols() -> None:
    dashboard = OrderFlowDashboard(symbols=SYMBOLS)
    state = _state()

    assert dashboard.merge(state) is True
    assert dashboard.merge(state) is False
    rendered = format_order_flow_dashboard(dashboard, refreshed_at=NOW)

    assert "ORDER FLOW | SIP L1 | 5 SYMBOLS" in rendered
    assert "ASTS | BUY_PRESSURE" in rendered
    assert "BID 99.9900 | ASK 100.0100 | SPREAD 2.0000bps" in rendered
    assert "CVD +300.0000" in rendered
    assert "5s D+60.0000 T12 P+4.0000bps" in rendered
    assert "ASTX | PENDIENTE" in rendered


def _state() -> OrderFlowState:
    windows = tuple(
        OrderFlowWindow(
            window_seconds=seconds,
            trade_count=12,
            buy_volume=Decimal("100"),
            sell_volume=Decimal("40"),
            neutral_volume=Decimal("0"),
            unknown_volume=Decimal("0"),
            delta=Decimal("60"),
            volume_velocity=Decimal("10"),
            large_buy_volume=Decimal("20"),
            large_sell_volume=Decimal("10"),
            price_change_bps=Decimal("4"),
        )
        for seconds in (1, 5, 15, 60, 300)
    )
    return OrderFlowState(
        symbol="ASTS",
        occurred_at=NOW,
        engine_version="1.1.0",
        state=OrderFlowStateKind.BUY_PRESSURE,
        current_price=Decimal("100"),
        mid_price=Decimal("100"),
        bid_price=Decimal("99.99"),
        ask_price=Decimal("100.01"),
        spread_bps=Decimal("2.0000"),
        cumulative_delta=Decimal("300"),
        confidence=Decimal("0.81"),
        data_quality=Decimal("0.93"),
        quote_age_ms=Decimal("120"),
        quote_fresh=True,
        unknown_trade_ratio=Decimal("0"),
        windows=windows,
        reasons=("buy_pressure",),
        context_hash="sha256:" + "a" * 64,
    )
