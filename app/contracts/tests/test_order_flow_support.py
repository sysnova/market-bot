from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts._base import new_uuid7
from app.contracts.order_flow import OrderFlowStateKind
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)

NOW = datetime(2026, 8, 24, 15, 0, tzinfo=UTC)


def _assessment(**updates: object) -> OrderFlowSupportAssessment:
    values: dict[str, object] = {
        "symbol": "AAPL",
        "occurred_at": NOW,
        "engine_version": "1.0.0",
        "disposition": OrderFlowSupportDisposition.CONFIRMS_SUPPORT,
        "support_assessment_id": new_uuid7(),
        "order_flow_state_id": new_uuid7(),
        "support_occurred_at": NOW - timedelta(days=1),
        "order_flow_occurred_at": NOW - timedelta(seconds=10),
        "current_price": Decimal("100"),
        "zone_low": Decimal("98"),
        "zone_high": Decimal("101"),
        "order_flow_state": OrderFlowStateKind.SELLER_EXHAUSTION,
        "confidence": Decimal("0.80"),
        "data_quality": Decimal("0.90"),
        "quote_fresh": True,
        "fresh_until": NOW + timedelta(seconds=110),
        "reasons": ("seller_exhaustion_over_support",),
        "context_hash": f"sha256:{'a' * 64}",
    }
    values.update(updates)
    return OrderFlowSupportAssessment(**values)  # type: ignore[arg-type]


def test_order_flow_support_contract_links_evidence_and_expiry() -> None:
    assessment = _assessment()

    assert assessment.support_assessment_id.version == 7
    assert assessment.order_flow_state_id.version == 7
    assert assessment.fresh_until > assessment.order_flow_occurred_at


def test_order_flow_support_contract_requires_ordered_native_support_zone() -> None:
    with pytest.raises(ValidationError, match="zone_low cannot exceed zone_high"):
        _assessment(zone_low=Decimal("102"), zone_high=Decimal("101"))


def test_directional_disposition_cannot_be_backed_by_neutral_flow() -> None:
    with pytest.raises(ValidationError, match="directional disposition"):
        _assessment(order_flow_state=OrderFlowStateKind.NEUTRAL)
