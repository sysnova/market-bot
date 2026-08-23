"""Shadow Order Flow annotation over 4HGERI-owned LONG support zones."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from app.contracts import GeriAssessment, GeriMaturity, NamedValue, TradeSide
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)

from .models import Swing4HGeriContext
from .v16 import Swing4HGeriEngineV16

_NON_ACTIONABLE = {
    GeriMaturity.BUILDING,
    GeriMaturity.EXTENDED,
    GeriMaturity.RECLAIM_REQUIRED,
    GeriMaturity.INVALIDATED,
}


class Swing4HGeriEngineV17(Swing4HGeriEngineV16):
    """Append fresh Order Flow evidence without changing GERI structure or maturity."""

    engine_version = "1.7.0"

    def analyze(self, context: Swing4HGeriContext) -> GeriAssessment:
        result = super().analyze(context)
        reference_at = context.current_price_at or context.as_of
        evidence = context.order_flow_support
        matched_zone = _matched_native_zone(result, evidence, as_of=reference_at)
        if evidence is None or matched_zone is None:
            return result.model_copy(update={"engine_version": self.engine_version})
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "reasons": tuple(
                    dict.fromkeys((*result.reasons, _reason(evidence.disposition)))
                ),
                "metrics": _upsert_metrics(result, *_metrics(evidence, matched_zone)),
                "context_hash": _enriched_hash(result.context_hash, evidence),
            }
        )


def _matched_native_zone(
    result: GeriAssessment,
    evidence: OrderFlowSupportAssessment | None,
    *,
    as_of: datetime | None,
) -> str | None:
    if (
        evidence is None
        or as_of is None
        or evidence.disposition is OrderFlowSupportDisposition.NEUTRAL
        or not evidence.fresh_at_assessment
        or not evidence.quote_fresh
        or evidence.occurred_at > as_of
        or evidence.fresh_until < as_of
        or evidence.symbol != result.symbol
    ):
        return None
    if (
        result.trade_side is TradeSide.LONG
        and result.maturity not in _NON_ACTIONABLE
        and result.zone_low is not None
        and result.zone_high is not None
        and _overlaps(result.zone_low, result.zone_high, evidence.zone_low, evidence.zone_high)
    ):
        return "MAIN"

    values = {item.name: item.value for item in result.metrics}
    side = getattr(values.get("countertrend_side"), "value", values.get("countertrend_side"))
    state = getattr(
        values.get("countertrend_state"), "value", values.get("countertrend_state")
    )
    low = _decimal(values.get("countertrend_zone_low"))
    high = _decimal(values.get("countertrend_zone_high"))
    if (
        side == TradeSide.LONG.value
        and state not in {item.value for item in _NON_ACTIONABLE}
        and values.get("countertrend_eligible") is True
        and values.get("countertrend_expired") is not True
        and low is not None
        and high is not None
        and _overlaps(low, high, evidence.zone_low, evidence.zone_high)
    ):
        return "TACTICAL"
    return None


def _metrics(
    evidence: OrderFlowSupportAssessment, matched_zone: str
) -> tuple[NamedValue, ...]:
    return (
        NamedValue(name="order_flow_support_assessment_id", value=str(evidence.assessment_id)),
        NamedValue(name="order_flow_state_id", value=str(evidence.order_flow_state_id)),
        NamedValue(name="order_flow_support_source_id", value=str(evidence.support_assessment_id)),
        NamedValue(name="order_flow_support_disposition", value=evidence.disposition.value),
        NamedValue(name="order_flow_state", value=evidence.order_flow_state.value),
        NamedValue(name="order_flow_confidence", value=evidence.confidence),
        NamedValue(name="order_flow_data_quality", value=evidence.data_quality),
        NamedValue(name="order_flow_native_zone_match", value=matched_zone),
        NamedValue(name="order_flow_context_hash", value=evidence.context_hash),
    )


def _reason(disposition: OrderFlowSupportDisposition) -> str:
    if disposition is OrderFlowSupportDisposition.CONFIRMS_SUPPORT:
        return "order_flow_confirms_support"
    return "order_flow_warns_breakdown"


def _decimal(value: object) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _overlaps(a_low: Decimal, a_high: Decimal, b_low: Decimal, b_high: Decimal) -> bool:
    return max(a_low, b_low) <= min(a_high, b_high)


def _upsert_metrics(result: GeriAssessment, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _enriched_hash(context_hash: str, evidence: OrderFlowSupportAssessment) -> str:
    payload = f"{context_hash}|order-flow-support:{evidence.context_hash}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
