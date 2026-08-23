from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.contracts._base import new_uuid7
from app.contracts.order_flow import OrderFlowStateKind
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)
from app.swing_trade_engine.models import SwingTradeContext
from app.swing_trade_engine.tests.test_engine import _confirmation_bars, daily_bars
from app.swing_trade_engine.v14 import SwingTradeEngineV14
from app.swing_trade_engine.v15 import SwingTradeEngineV15


def test_v15_confirms_support_without_promoting_swingtrade_maturity() -> None:
    bars = daily_bars()
    confirmations = _confirmation_bars(bars)
    as_of = confirmations[-1].timestamp + timedelta(minutes=15)
    base = SwingTradeContext(
        symbol="AAPL",
        as_of=as_of,
        current_price=Decimal("97"),
        daily_bars=bars,
        confirmation_bars=confirmations,
        current_price_at=as_of,
    )
    native = SwingTradeEngineV14().analyze(base)
    evidence = OrderFlowSupportAssessment(
        symbol="AAPL",
        occurred_at=as_of,
        engine_version="1.0.0",
        disposition=OrderFlowSupportDisposition.CONFIRMS_SUPPORT,
        support_assessment_id=new_uuid7(),
        order_flow_state_id=new_uuid7(),
        support_occurred_at=bars[-1].timestamp,
        order_flow_occurred_at=as_of,
        current_price=Decimal("97"),
        zone_low=native.zone_low,
        zone_high=native.zone_high,
        order_flow_state=OrderFlowStateKind.BULLISH_DIVERGENCE,
        confidence=Decimal("0.75"),
        data_quality=Decimal("0.80"),
        quote_fresh=True,
        fresh_until=as_of + timedelta(seconds=120),
        fresh_at_assessment=True,
        reasons=("bullish_divergence_over_support",),
        context_hash=f"sha256:{'f' * 64}",
    )

    enriched = SwingTradeEngineV15().analyze(replace(base, order_flow_support=evidence))
    metrics = {item.name: item.value for item in enriched.metrics}

    assert enriched.maturity is native.maturity
    assert enriched.eligible is native.eligible
    assert (enriched.zone_low, enriched.zone_high, enriched.invalidation) == (
        native.zone_low,
        native.zone_high,
        native.invalidation,
    )
    assert metrics["order_flow_support_disposition"] == "CONFIRMS_SUPPORT"
    assert "order_flow_confirms_support" in enriched.reasons
