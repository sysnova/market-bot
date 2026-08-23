from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts.order_flow import OrderFlowState, OrderFlowStateKind, OrderFlowWindow
from app.contracts.scalp import ScalpDirection, ScalpExitReason, ScalpSetup, ScalpState
from app.scalp_engine.engine import ScalpEngine
from app.scalp_engine.models import ScalpContext

NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _window(seconds: int) -> OrderFlowWindow:
    return OrderFlowWindow(
        window_seconds=seconds,
        trade_count=20,
        buy_volume=Decimal("1200"),
        sell_volume=Decimal("600"),
        neutral_volume=Decimal("0"),
        unknown_volume=Decimal("50"),
        delta=Decimal("600"),
        volume_velocity=Decimal("180"),
        large_buy_volume=Decimal("500"),
        large_sell_volume=Decimal("100"),
        price_change_bps=Decimal("8"),
    )


def _flow(
    *,
    occurred_at: datetime = NOW,
    state: OrderFlowStateKind = OrderFlowStateKind.SELLER_EXHAUSTION,
    confidence: Decimal = Decimal("0.82"),
) -> OrderFlowState:
    return OrderFlowState(
        symbol="AAPL",
        occurred_at=occurred_at,
        engine_version="1.0.0",
        state=state,
        current_price=Decimal("100.50"),
        mid_price=Decimal("100.50"),
        cumulative_delta=Decimal("2400"),
        confidence=confidence,
        data_quality=Decimal("0.92"),
        quote_age_ms=Decimal("100"),
        quote_fresh=True,
        unknown_trade_ratio=Decimal("0.05"),
        windows=tuple(_window(seconds) for seconds in (1, 5, 15, 60, 300)),
        reasons=("seller_exhaustion",),
        context_hash="sha256:" + "b" * 64,
    )


def _context(**changes: object) -> ScalpContext:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "as_of": NOW,
        "current_price": Decimal("100.50"),
        "previous_price": Decimal("100.30"),
        "bid_price": Decimal("100.49"),
        "ask_price": Decimal("100.51"),
        "session_vwap": Decimal("101"),
        "atr": Decimal("1"),
        "support_low": Decimal("100"),
        "support_high": Decimal("100.80"),
        "order_flow": _flow(),
    }
    values.update(changes)
    return ScalpContext.model_validate(values)


def test_support_reversal_matures_causally_and_exits_at_target() -> None:
    engine = ScalpEngine()

    armed = engine.evaluate(_context())
    assert armed.assessment.state is ScalpState.ARMED
    assert armed.assessment.setup is ScalpSetup.SUPPORT_REVERSAL
    assert armed.transition is not None

    entered = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=1),
            current_price=Decimal("100.70"),
            bid_price=Decimal("100.69"),
            ask_price=Decimal("100.71"),
            order_flow=_flow(occurred_at=NOW + timedelta(seconds=1)),
            previous_assessment=armed.assessment,
        )
    )
    assert entered.assessment.state is ScalpState.ENTRY_CONFIRMED
    assert entered.assessment.entry_confirmed_at == NOW + timedelta(seconds=1)

    managing = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=2),
            current_price=Decimal("101"),
            bid_price=Decimal("100.99"),
            ask_price=Decimal("101.01"),
            order_flow=_flow(occurred_at=NOW + timedelta(seconds=2)),
            previous_assessment=entered.assessment,
        )
    )
    assert managing.assessment.state is ScalpState.MANAGING

    target = managing.assessment.target
    assert target is not None
    exited = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=3),
            current_price=target,
            bid_price=target,
            ask_price=target + Decimal("0.01"),
            order_flow=_flow(occurred_at=NOW + timedelta(seconds=3)),
            previous_assessment=managing.assessment,
        )
    )
    assert exited.assessment.state is ScalpState.EXIT_CONFIRMED
    assert exited.assessment.exit_reason is ScalpExitReason.TARGET


def test_vwap_reclaim_arms_without_long_or_swing_context() -> None:
    result = ScalpEngine().evaluate(
        _context(
            current_price=Decimal("100.10"),
            previous_price=Decimal("99.90"),
            bid_price=Decimal("100.09"),
            ask_price=Decimal("100.11"),
            session_vwap=Decimal("100"),
            support_low=None,
            support_high=None,
            order_flow=_flow(state=OrderFlowStateKind.BUY_PRESSURE),
        )
    )

    assert result.assessment.state is ScalpState.ARMED
    assert result.assessment.setup is ScalpSetup.VWAP_RECLAIM


def test_vwap_rejection_matures_a_short_and_exits_at_target() -> None:
    engine = ScalpEngine()
    bearish = _flow(state=OrderFlowStateKind.SELL_PRESSURE)
    armed = engine.evaluate(
        _context(
            current_price=Decimal("99.90"),
            previous_price=Decimal("100.10"),
            bid_price=Decimal("99.89"),
            ask_price=Decimal("99.91"),
            session_vwap=Decimal("100"),
            support_low=None,
            support_high=None,
            order_flow=bearish,
        )
    ).assessment

    assert armed.state is ScalpState.ARMED
    assert armed.setup is ScalpSetup.VWAP_REJECTION
    assert armed.direction is ScalpDirection.SHORT
    entered = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=1),
            current_price=Decimal("99.80"),
            previous_price=Decimal("99.90"),
            bid_price=Decimal("99.79"),
            ask_price=Decimal("99.81"),
            session_vwap=Decimal("100"),
            support_low=None,
            support_high=None,
            order_flow=_flow(
                occurred_at=NOW + timedelta(seconds=1),
                state=OrderFlowStateKind.SELL_PRESSURE,
            ),
            previous_assessment=armed,
        )
    ).assessment

    assert entered.state is ScalpState.ENTRY_CONFIRMED
    target = entered.target
    assert target is not None
    exited = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=2),
            current_price=target,
            previous_price=Decimal("99.80"),
            bid_price=target - Decimal("0.01"),
            ask_price=target,
            session_vwap=Decimal("100"),
            support_low=None,
            support_high=None,
            order_flow=_flow(
                occurred_at=NOW + timedelta(seconds=2),
                state=OrderFlowStateKind.SELL_PRESSURE,
            ),
            previous_assessment=entered,
        )
    ).assessment

    assert exited.state is ScalpState.EXIT_CONFIRMED
    assert exited.exit_reason is ScalpExitReason.TARGET


def test_stale_order_flow_keeps_the_engine_watching() -> None:
    result = ScalpEngine().evaluate(
        _context(order_flow=_flow(occurred_at=NOW - timedelta(seconds=10)))
    )

    assert result.assessment.state is ScalpState.WATCHING
    assert "order_flow_stale" in result.assessment.reasons
    assert result.assessment.entry_price is None


def test_armed_setup_invalidates_below_structural_stop() -> None:
    engine = ScalpEngine()
    armed = engine.evaluate(_context()).assessment
    invalidation = armed.invalidation
    assert invalidation is not None

    result = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=1),
            current_price=invalidation - Decimal("0.01"),
            previous_price=Decimal("100.50"),
            bid_price=invalidation - Decimal("0.02"),
            ask_price=invalidation,
            order_flow=_flow(occurred_at=NOW + timedelta(seconds=1)),
            previous_assessment=armed,
        )
    )

    assert result.assessment.state is ScalpState.INVALIDATED
    assert result.assessment.exit_reason is ScalpExitReason.SETUP_INVALIDATED


def test_terminal_setup_can_rearm_for_another_round_trip_after_cooldown() -> None:
    engine = ScalpEngine()
    armed = engine.evaluate(_context()).assessment
    invalidation = armed.invalidation
    assert invalidation is not None
    invalidated = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=1),
            current_price=invalidation - Decimal("0.01"),
            bid_price=invalidation - Decimal("0.02"),
            ask_price=invalidation,
            order_flow=_flow(occurred_at=NOW + timedelta(seconds=1)),
            previous_assessment=armed,
        )
    ).assessment

    rearmed = engine.evaluate(
        _context(
            as_of=NOW + timedelta(seconds=62),
            order_flow=_flow(occurred_at=NOW + timedelta(seconds=62)),
            previous_assessment=invalidated,
        )
    )

    assert rearmed.assessment.state is ScalpState.ARMED
    assert rearmed.transition is not None
    assert rearmed.transition.previous_state is ScalpState.INVALIDATED


def test_managing_setup_exits_after_max_hold() -> None:
    engine = ScalpEngine()
    armed = engine.evaluate(_context()).assessment
    entered_at = NOW + timedelta(seconds=1)
    entered = engine.evaluate(
        _context(
            as_of=entered_at,
            order_flow=_flow(occurred_at=entered_at),
            previous_assessment=armed,
        )
    ).assessment
    managing = engine.evaluate(
        _context(
            as_of=entered_at + timedelta(seconds=1),
            order_flow=_flow(occurred_at=entered_at + timedelta(seconds=1)),
            previous_assessment=entered,
        )
    ).assessment

    result = engine.evaluate(
        _context(
            as_of=entered_at + timedelta(seconds=901),
            order_flow=_flow(occurred_at=entered_at + timedelta(seconds=901)),
            previous_assessment=managing,
        )
    )

    assert result.assessment.state is ScalpState.EXIT_CONFIRMED
    assert result.assessment.exit_reason is ScalpExitReason.MAX_HOLD


def test_same_context_produces_the_same_assessment_identifier() -> None:
    engine = ScalpEngine()
    context = _context()

    first = engine.evaluate(context)
    second = engine.evaluate(context)

    assert first == second
    assert first.assessment.assessment_id.version == 7
