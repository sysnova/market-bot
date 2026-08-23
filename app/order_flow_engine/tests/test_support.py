from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.contracts.enums import SupportState
from app.contracts.order_flow import (
    ORDER_FLOW_WINDOWS,
    OrderFlowState,
    OrderFlowStateKind,
    OrderFlowWindow,
)
from app.contracts.order_flow_support import OrderFlowSupportDisposition
from app.contracts.support_confirmation import SupportAssessment
from app.order_flow_engine.support import OrderFlowSupportPolicy, assess_support_order_flow

NOW = datetime(2026, 8, 24, 15, 0, tzinfo=UTC)


def _window(seconds: int) -> OrderFlowWindow:
    return OrderFlowWindow(
        window_seconds=seconds,
        trade_count=20,
        buy_volume=Decimal("700"),
        sell_volume=Decimal("300"),
        neutral_volume=Decimal("0"),
        unknown_volume=Decimal("0"),
        delta=Decimal("400"),
        volume_velocity=Decimal("10"),
        large_buy_volume=Decimal("0"),
        large_sell_volume=Decimal("0"),
        price_change_bps=Decimal("1"),
    )


def _flow(kind: OrderFlowStateKind, **updates: object) -> OrderFlowState:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "occurred_at": NOW - timedelta(seconds=10),
        "engine_version": "1.0.0",
        "state": kind,
        "current_price": Decimal("100"),
        "mid_price": Decimal("100"),
        "confidence": Decimal("0.80"),
        "data_quality": Decimal("0.90"),
        "quote_age_ms": Decimal("50"),
        "quote_fresh": True,
        "unknown_trade_ratio": Decimal("0"),
        "windows": tuple(_window(seconds) for seconds in ORDER_FLOW_WINDOWS),
        "reasons": (kind.value.lower(),),
        "context_hash": f"sha256:{'b' * 64}",
    }
    values.update(updates)
    return OrderFlowState(**values)  # type: ignore[arg-type]


def _support(**updates: object) -> SupportAssessment:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "occurred_at": NOW - timedelta(days=1),
        "engine_version": "0.3.0",
        "state": SupportState.REACTION_CONFIRMED,
        "current_price": Decimal("100"),
        "zone_low": Decimal("98"),
        "zone_center": Decimal("99.5"),
        "zone_high": Decimal("101"),
        "invalidation": Decimal("97"),
        "support_score": Decimal("80"),
        "reaction_score": Decimal("75"),
        "reversal_score": Decimal("65"),
        "confidence": Decimal("0.80"),
        "reasons": ("fixture",),
        "context_hash": f"sha256:{'c' * 64}",
    }
    values.update(updates)
    return SupportAssessment(**values)  # type: ignore[arg-type]


def test_assessment_confirms_or_warns_without_changing_support_geometry() -> None:
    confirmation = assess_support_order_flow(
        _flow(OrderFlowStateKind.SELLER_EXHAUSTION), _support(), as_of=NOW
    )
    warning = assess_support_order_flow(
        _flow(OrderFlowStateKind.SELL_PRESSURE), _support(), as_of=NOW
    )

    assert confirmation.disposition is OrderFlowSupportDisposition.CONFIRMS_SUPPORT
    assert warning.disposition is OrderFlowSupportDisposition.WARNS_BREAKDOWN
    assert (confirmation.zone_low, confirmation.zone_high) == (
        Decimal("98"),
        Decimal("101"),
    )
    repeated = assess_support_order_flow(
        _flow(OrderFlowStateKind.SELLER_EXHAUSTION), _support(), as_of=NOW
    )
    assert repeated.assessment_id == confirmation.assessment_id


def test_stale_or_low_quality_flow_is_neutral_and_not_fresh() -> None:
    stale = assess_support_order_flow(
        _flow(
            OrderFlowStateKind.BUY_PRESSURE,
            occurred_at=NOW - timedelta(minutes=5),
        ),
        _support(),
        as_of=NOW,
    )
    low_quality = assess_support_order_flow(
        _flow(OrderFlowStateKind.BUY_PRESSURE, data_quality=Decimal("0.20")),
        _support(),
        as_of=NOW,
    )

    assert stale.disposition is OrderFlowSupportDisposition.NEUTRAL
    assert stale.fresh_at_assessment is False
    assert "order_flow_stale" in stale.reasons
    assert low_quality.disposition is OrderFlowSupportDisposition.NEUTRAL
    assert "order_flow_quality_below_threshold" in low_quality.reasons


def test_price_outside_support_zone_is_neutral() -> None:
    assessment = assess_support_order_flow(
        _flow(OrderFlowStateKind.BUY_PRESSURE, current_price=Decimal("105")),
        _support(),
        as_of=NOW,
    )

    assert assessment.disposition is OrderFlowSupportDisposition.NEUTRAL
    assert "price_outside_support_zone" in assessment.reasons


def test_policy_rejects_symbol_mismatch_and_requires_complete_support_zone() -> None:
    policy = OrderFlowSupportPolicy(max_order_flow_age=timedelta(seconds=30))
    mismatched = _flow(OrderFlowStateKind.BUY_PRESSURE, symbol="MSFT")

    try:
        assess_support_order_flow(mismatched, _support(), as_of=NOW, policy=policy)
    except ValueError as exc:
        assert "symbols" in str(exc)
    else:
        raise AssertionError("symbol mismatch must be rejected")
