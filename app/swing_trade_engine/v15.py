"""Shadow Order Flow annotation over SwingTrade-owned Fibonacci geometry."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from app.contracts import NamedValue, SwingTradeAssessment
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)

from .models import SwingTradeContext
from .v14 import SwingTradeEngineV14


class SwingTradeEngineV15(SwingTradeEngineV14):
    """Append fresh microstructure context without promoting SwingTrade maturity."""

    engine_version = "1.5.0"

    def analyze(self, context: SwingTradeContext) -> SwingTradeAssessment:
        result = super().analyze(context)
        evidence = context.order_flow_support
        reference_at = context.current_price_at or context.as_of
        if not _matches_native_zone(result, evidence, as_of=reference_at):
            return result.model_copy(update={"engine_version": self.engine_version})
        assert evidence is not None
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "reasons": tuple(
                    dict.fromkeys((*result.reasons, _reason(evidence.disposition)))
                ),
                "metrics": _upsert_metrics(result, *_metrics(evidence)),
                "context_hash": _enriched_hash(result.context_hash, evidence),
            }
        )


def _matches_native_zone(
    result: SwingTradeAssessment,
    evidence: OrderFlowSupportAssessment | None,
    *,
    as_of: datetime,
) -> bool:
    return bool(
        evidence is not None
        and evidence.disposition is not OrderFlowSupportDisposition.NEUTRAL
        and evidence.fresh_at_assessment
        and evidence.quote_fresh
        and evidence.occurred_at <= as_of <= evidence.fresh_until
        and evidence.symbol == result.symbol
        and _overlaps(result.zone_low, result.zone_high, evidence.zone_low, evidence.zone_high)
    )


def _metrics(evidence: OrderFlowSupportAssessment) -> tuple[NamedValue, ...]:
    return (
        NamedValue(name="order_flow_support_assessment_id", value=str(evidence.assessment_id)),
        NamedValue(name="order_flow_state_id", value=str(evidence.order_flow_state_id)),
        NamedValue(name="order_flow_support_source_id", value=str(evidence.support_assessment_id)),
        NamedValue(name="order_flow_support_disposition", value=evidence.disposition.value),
        NamedValue(name="order_flow_state", value=evidence.order_flow_state.value),
        NamedValue(name="order_flow_confidence", value=evidence.confidence),
        NamedValue(name="order_flow_data_quality", value=evidence.data_quality),
        NamedValue(name="order_flow_native_zone_match", value="SWING_TRADE_ZONE"),
        NamedValue(name="order_flow_context_hash", value=evidence.context_hash),
    )


def _reason(disposition: OrderFlowSupportDisposition) -> str:
    if disposition is OrderFlowSupportDisposition.CONFIRMS_SUPPORT:
        return "order_flow_confirms_support"
    return "order_flow_warns_breakdown"


def _overlaps(a_low: Decimal, a_high: Decimal, b_low: Decimal, b_high: Decimal) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)


def _upsert_metrics(
    result: SwingTradeAssessment, *items: NamedValue
) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _enriched_hash(context_hash: str, evidence: OrderFlowSupportAssessment) -> str:
    payload = f"{context_hash}|order-flow-support:{evidence.context_hash}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
