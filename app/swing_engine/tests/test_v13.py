from datetime import timedelta
from decimal import Decimal

from app.contracts._base import new_uuid7
from app.contracts.order_flow import OrderFlowStateKind
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)
from app.swing_engine.tests.test_v11 import _bullish_context
from app.swing_engine.v12 import SwingEngineV12
from app.swing_engine.v13 import SwingEngineV13


def _order_flow_support(context: object, *, stale: bool = False) -> OrderFlowSupportAssessment:
    as_of = context.as_of  # type: ignore[attr-defined]
    native = SwingEngineV12().analyze(context)  # type: ignore[arg-type]
    values = {item.name: item.value for item in native.metrics}
    zone_low = values["entry_zone_low"]
    zone_high = values["entry_zone_high"]
    occurred_at = as_of - timedelta(minutes=3 if stale else 0)
    return OrderFlowSupportAssessment(
        symbol=context.symbol,  # type: ignore[attr-defined]
        occurred_at=occurred_at,
        engine_version="1.0.0",
        disposition=OrderFlowSupportDisposition.CONFIRMS_SUPPORT,
        support_assessment_id=new_uuid7(),
        order_flow_state_id=new_uuid7(),
        support_occurred_at=as_of - timedelta(days=1),
        order_flow_occurred_at=occurred_at,
        current_price=context.price,  # type: ignore[attr-defined]
        zone_low=zone_low,
        zone_high=zone_high,
        order_flow_state=OrderFlowStateKind.SELLER_EXHAUSTION,
        confidence=Decimal("0.80"),
        data_quality=Decimal("0.90"),
        quote_fresh=True,
        fresh_until=occurred_at + timedelta(seconds=120),
        fresh_at_assessment=True,
        reasons=("seller_exhaustion_over_support",),
        context_hash=f"sha256:{'d' * 64}",
    )


def test_v13_appends_fresh_order_flow_only_when_native_zone_matches() -> None:
    base = _bullish_context()
    evidence = _order_flow_support(base)
    native = SwingEngineV12().analyze(base)
    enriched = SwingEngineV13().analyze(
        base.model_copy(update={"order_flow_support": evidence})
    )
    metrics = {item.name: item.value for item in enriched.metrics}

    assert enriched.verdict is native.verdict
    assert enriched.direction is native.direction
    assert enriched.score == native.score
    assert metrics["order_flow_support_disposition"] == "CONFIRMS_SUPPORT"
    assert "order_flow_confirms_support" in enriched.reasons
    assert evidence.assessment_id in enriched.source_event_ids


def test_v13_ignores_stale_order_flow() -> None:
    base = _bullish_context()
    stale = _order_flow_support(base, stale=True)

    result = SwingEngineV13().analyze(base.model_copy(update={"order_flow_support": stale}))

    assert not any(item.name.startswith("order_flow_") for item in result.metrics)
