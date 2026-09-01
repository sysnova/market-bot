from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts import (
    OrderFlowState,
    OrderFlowStateKind,
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
    OrderFlowWindow,
    new_uuid7,
)
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
    dashboard = OrderFlowDashboard(symbols=SYMBOLS, expected_engine_version="1.1.0")
    state = _state()

    assert dashboard.merge(state) is True
    assert dashboard.merge(state) is False
    rendered = format_order_flow_dashboard(dashboard, refreshed_at=NOW)

    assert "ORDER FLOW | SIP L1 | 5 SYMBOLS" in rendered
    assert "ENGINE 1.1.0" in rendered
    assert "ESTADO | RECIBIENDO ASSESSMENTS" in rendered
    assert "ASTS | BUY_PRESSURE" in rendered
    assert "BID 99.9900 | ASK 100.0100 | SPREAD 2.0000bps" in rendered
    assert "CVD +300.0000" in rendered
    assert "5s D+60.0000 T12 P+4.0000bps" in rendered
    assert "ASTX | PENDIENTE" in rendered


def test_empty_order_flow_dashboard_explains_that_it_is_waiting_for_market_events() -> None:
    rendered = format_order_flow_dashboard(
        OrderFlowDashboard(symbols=SYMBOLS, expected_engine_version="1.1.0"),
        refreshed_at=NOW,
    )

    assert "ESTADO | ESPERANDO EVENTOS DE MERCADO" in rendered
    assert "Todavia no se recibio ningun assessment de Order Flow" in rendered
    assert "ASTS | PENDIENTE" in rendered


def test_dashboard_ignores_replayed_states_from_an_old_engine_version() -> None:
    dashboard = OrderFlowDashboard(symbols=SYMBOLS, expected_engine_version="1.1.0")

    assert dashboard.merge(_state().model_copy(update={"engine_version": "1.0.0"})) is True
    rendered = format_order_flow_dashboard(dashboard, refreshed_at=NOW)

    assert "IGNORADOS | assessment incompatible 1.0.0" in rendered
    assert "ASTS | PENDIENTE" in rendered


def test_dashboard_translates_stable_flow_and_support_into_an_actionable_plan() -> None:
    dashboard = OrderFlowDashboard(symbols=SYMBOLS, expected_engine_version="1.2.0")
    state = _state().model_copy(
        update={
            "engine_version": "1.2.0",
            "pulse_state": OrderFlowStateKind.SELLER_EXHAUSTION,
            "state_stable_since": NOW - timedelta(seconds=27),
            "candidate_state": OrderFlowStateKind.SELLER_EXHAUSTION,
            "candidate_samples": 2,
        }
    )

    assert dashboard.merge(state) is True
    assert dashboard.merge_support(_support(state)) is True
    rendered = format_order_flow_dashboard(dashboard, refreshed_at=NOW)

    assert "ASTS | REGIMEN COMPRADOR" in rendered
    assert "ACCION PREPARAR_LONG" in rendered
    assert "ESTABLE PRESION COMPRADORA 27s" in rendered
    assert "PULSO AGOTAMIENTO VENDEDOR (ALCISTA)" in rendered
    assert "CANDIDATO AGOTAMIENTO VENDEDOR (ALCISTA) 2" in rendered
    assert "SOPORTE 99.5000-100.5000 | CONFIRMA SOPORTE" in rendered
    assert "TRIGGER > 100.5000 | RIESGO < 99.5000" in rendered


def test_dashboard_distinguishes_reclaim_trigger_from_an_extended_entry() -> None:
    triggered = OrderFlowDashboard(symbols=SYMBOLS, expected_engine_version="1.2.0")
    state = _state().model_copy(
        update={
            "engine_version": "1.2.0",
            "pulse_state": OrderFlowStateKind.BUY_PRESSURE,
            "state_stable_since": NOW - timedelta(seconds=30),
            "current_price": Decimal("100.60"),
        }
    )
    triggered.merge(state)
    triggered.merge_support(_support(state))

    assert "ACCION LONG_TRIGGERED" in format_order_flow_dashboard(
        triggered,
        refreshed_at=NOW,
    )

    extended = OrderFlowDashboard(symbols=SYMBOLS, expected_engine_version="1.2.0")
    extended_state = state.model_copy(update={"current_price": Decimal("101.60")})
    extended.merge(extended_state)
    extended.merge_support(_support(extended_state))

    assert "ACCION NO_PERSEGUIR_EXTENDIDO" in format_order_flow_dashboard(
        extended,
        refreshed_at=NOW,
    )


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


def _support(state: OrderFlowState) -> OrderFlowSupportAssessment:
    return OrderFlowSupportAssessment(
        symbol=state.symbol,
        occurred_at=NOW,
        engine_version="1.0.0",
        disposition=OrderFlowSupportDisposition.CONFIRMS_SUPPORT,
        support_assessment_id=new_uuid7(),
        order_flow_state_id=state.state_id,
        support_occurred_at=NOW - timedelta(minutes=5),
        order_flow_occurred_at=state.occurred_at,
        current_price=state.current_price,
        zone_low=Decimal("99.50"),
        zone_high=Decimal("100.50"),
        order_flow_state=state.state,
        confidence=Decimal("0.81"),
        data_quality=Decimal("0.93"),
        quote_fresh=True,
        fresh_until=NOW + timedelta(seconds=120),
        reasons=("buy_pressure_over_support",),
        context_hash="sha256:" + "b" * 64,
    )
