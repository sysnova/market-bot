"""Pure operational assessment of Order Flow over Support-owned geometry."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.contracts.order_flow import OrderFlowState, OrderFlowStateKind
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)
from app.contracts.support_confirmation import SupportAssessment

_CONFIRMING_STATES = {
    OrderFlowStateKind.BUY_PRESSURE,
    OrderFlowStateKind.SELLER_EXHAUSTION,
    OrderFlowStateKind.BUY_ABSORPTION,
    OrderFlowStateKind.BULLISH_DIVERGENCE,
}
_WARNING_STATES = {
    OrderFlowStateKind.SELL_PRESSURE,
    OrderFlowStateKind.BUYER_EXHAUSTION,
    OrderFlowStateKind.SELL_ABSORPTION,
    OrderFlowStateKind.BEARISH_DIVERGENCE,
}


@dataclass(frozen=True, slots=True)
class OrderFlowSupportPolicy:
    """Freshness and quality gates for support confluence."""

    max_order_flow_age: timedelta = timedelta(seconds=120)
    max_support_age: timedelta = timedelta(days=8)
    minimum_data_quality: Decimal = Decimal("0.60")
    minimum_confidence: Decimal = Decimal("0.50")

    def __post_init__(self) -> None:
        if self.max_order_flow_age <= timedelta(0) or self.max_support_age <= timedelta(0):
            raise ValueError("freshness windows must be positive")
        for name, value in (
            ("minimum_data_quality", self.minimum_data_quality),
            ("minimum_confidence", self.minimum_confidence),
        ):
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{name} must be in [0, 1]")


def assess_support_order_flow(
    order_flow: OrderFlowState,
    support: SupportAssessment,
    *,
    as_of: datetime,
    policy: OrderFlowSupportPolicy | None = None,
) -> OrderFlowSupportAssessment:
    """Classify microstructure while copying, never deriving, the Support zone."""

    selected = policy or OrderFlowSupportPolicy()
    if order_flow.symbol != support.symbol:
        raise ValueError("Order Flow and Support symbols must match")
    if support.zone_low is None or support.zone_high is None:
        raise ValueError("Order Flow Support requires a complete Support-owned zone")
    support_at = support.data_as_of or support.occurred_at
    if order_flow.occurred_at > as_of or support_at > as_of:
        raise ValueError("evidence cannot be later than as_of")

    flow_fresh_until = order_flow.occurred_at + selected.max_order_flow_age
    flow_fresh = order_flow.occurred_at <= as_of <= flow_fresh_until
    support_fresh = support_at >= as_of - selected.max_support_age
    quality_passed = order_flow.data_quality >= selected.minimum_data_quality
    confidence_passed = order_flow.confidence >= selected.minimum_confidence
    price_in_zone = support.zone_low <= order_flow.current_price <= support.zone_high
    usable = (
        flow_fresh
        and support_fresh
        and quality_passed
        and confidence_passed
        and order_flow.quote_fresh
        and price_in_zone
    )

    reasons: list[str] = []
    if not flow_fresh:
        reasons.append("order_flow_stale")
    if not support_fresh:
        reasons.append("support_stale")
    if not quality_passed:
        reasons.append("order_flow_quality_below_threshold")
    if not confidence_passed:
        reasons.append("order_flow_confidence_below_threshold")
    if not order_flow.quote_fresh:
        reasons.append("order_flow_quote_stale")
    if not price_in_zone:
        reasons.append("price_outside_support_zone")

    disposition = OrderFlowSupportDisposition.NEUTRAL
    if usable and order_flow.state in _CONFIRMING_STATES:
        disposition = OrderFlowSupportDisposition.CONFIRMS_SUPPORT
    elif usable and order_flow.state in _WARNING_STATES:
        disposition = OrderFlowSupportDisposition.WARNS_BREAKDOWN
    if not reasons:
        reasons.append(f"{order_flow.state.value.lower()}_over_support")

    context_hash = _context_hash(order_flow, support, as_of, selected)
    source_ids = tuple(
        dict.fromkeys(
            (order_flow.state_id, support.assessment_id, *order_flow.source_event_ids)
        )
    )
    return OrderFlowSupportAssessment(
        assessment_id=_stable_uuid7(as_of, context_hash),
        symbol=order_flow.symbol,
        occurred_at=as_of,
        engine_version="1.0.0",
        disposition=disposition,
        support_assessment_id=support.assessment_id,
        order_flow_state_id=order_flow.state_id,
        support_occurred_at=support_at,
        order_flow_occurred_at=order_flow.occurred_at,
        current_price=order_flow.current_price,
        zone_low=support.zone_low,
        zone_high=support.zone_high,
        order_flow_state=order_flow.state,
        confidence=min(order_flow.confidence, support.confidence),
        data_quality=order_flow.data_quality,
        quote_fresh=order_flow.quote_fresh,
        fresh_until=flow_fresh_until,
        fresh_at_assessment=flow_fresh and order_flow.quote_fresh,
        reasons=tuple(reasons),
        source_event_ids=source_ids,
        context_hash=context_hash,
    )


def _context_hash(
    order_flow: OrderFlowState,
    support: SupportAssessment,
    as_of: datetime,
    policy: OrderFlowSupportPolicy,
) -> str:
    payload = {
        "as_of": as_of.isoformat(),
        "flow": order_flow.context_hash,
        "support": support.context_hash,
        "max_flow_age_seconds": str(policy.max_order_flow_age.total_seconds()),
        "max_support_age_seconds": str(policy.max_support_age.total_seconds()),
        "minimum_data_quality": str(policy.minimum_data_quality),
        "minimum_confidence": str(policy.minimum_confidence),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _stable_uuid7(occurred_at: datetime, identity: str) -> UUID:
    timestamp_ms = int(occurred_at.timestamp() * 1_000) & ((1 << 48) - 1)
    random_bits = int.from_bytes(hashlib.sha256(identity.encode()).digest(), "big") & (
        (1 << 74) - 1
    )
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)
