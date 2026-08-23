from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.contracts._base import new_uuid7
from app.contracts.order_flow import OrderFlowStateKind
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)
from app.swing_4h_geri_engine.models import Swing4HGeriContext
from app.swing_4h_geri_engine.tests.test_geri_engine import level_three_bars
from app.swing_4h_geri_engine.v16 import Swing4HGeriEngineV16
from app.swing_4h_geri_engine.v17 import Swing4HGeriEngineV17


def test_v17_warns_about_breakdown_without_changing_geri_maturity_or_zone() -> None:
    bars = level_three_bars()
    as_of = bars[-1].timestamp
    base = Swing4HGeriContext(
        symbol="AAPL",
        bars=bars,
        current_price=Decimal("93.2"),
        as_of=as_of,
        current_price_at=as_of,
    )
    native = Swing4HGeriEngineV16().analyze(base)
    evidence = OrderFlowSupportAssessment(
        symbol="AAPL",
        occurred_at=as_of,
        engine_version="1.0.0",
        disposition=OrderFlowSupportDisposition.WARNS_BREAKDOWN,
        support_assessment_id=new_uuid7(),
        order_flow_state_id=new_uuid7(),
        support_occurred_at=as_of - timedelta(days=1),
        order_flow_occurred_at=as_of,
        current_price=Decimal("93.2"),
        zone_low=native.zone_low,
        zone_high=native.zone_high,
        order_flow_state=OrderFlowStateKind.SELL_PRESSURE,
        confidence=Decimal("0.85"),
        data_quality=Decimal("0.90"),
        quote_fresh=True,
        fresh_until=as_of + timedelta(seconds=120),
        fresh_at_assessment=True,
        reasons=("sell_pressure_over_support",),
        context_hash=f"sha256:{'e' * 64}",
    )

    enriched = Swing4HGeriEngineV17().analyze(
        replace(base, order_flow_support=evidence)
    )
    metrics = {item.name: item.value for item in enriched.metrics}

    assert enriched.maturity is native.maturity
    assert (enriched.zone_low, enriched.zone_high, enriched.invalidation) == (
        native.zone_low,
        native.zone_high,
        native.invalidation,
    )
    assert metrics["order_flow_support_disposition"] == "WARNS_BREAKDOWN"
    assert "order_flow_warns_breakdown" in enriched.reasons
