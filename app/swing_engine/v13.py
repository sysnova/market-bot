"""Shadow Order Flow annotation over Swing-owned support geometry."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.contracts import AnalysisResult, NamedValue
from app.contracts.order_flow_support import (
    OrderFlowSupportAssessment,
    OrderFlowSupportDisposition,
)

from .models import SwingContext
from .v12 import SwingEngineV12


class SwingEngineV13(SwingEngineV12):
    """Append fresh microstructure evidence without changing Swing decisions."""

    engine_version = "13.0.0"

    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult:
        result = super().analyze(context, source_event_ids=source_event_ids)
        evidence = context.order_flow_support
        matched_zone = _matched_native_zone(result, evidence, as_of=context.as_of)
        if evidence is None or matched_zone is None:
            return result.model_copy(update={"engine_version": self.engine_version})
        return result.model_copy(
            update={
                "engine_version": self.engine_version,
                "reasons": tuple(
                    dict.fromkeys((*result.reasons, _reason(evidence.disposition)))
                ),
                "metrics": _upsert_metrics(
                    result,
                    *_metrics(evidence, matched_zone),
                ),
                "source_event_ids": tuple(
                    dict.fromkeys(
                        (
                            *result.source_event_ids,
                            evidence.assessment_id,
                            evidence.order_flow_state_id,
                            evidence.support_assessment_id,
                            *evidence.source_event_ids,
                        )
                    )
                ),
                "context_hash": _enriched_hash(result.context_hash, evidence),
            }
        )


def _matched_native_zone(
    result: AnalysisResult,
    evidence: OrderFlowSupportAssessment | None,
    *,
    as_of: datetime,
) -> str | None:
    if (
        evidence is None
        or evidence.disposition is OrderFlowSupportDisposition.NEUTRAL
        or not evidence.fresh_at_assessment
        or not evidence.quote_fresh
        or evidence.occurred_at > as_of
        or evidence.fresh_until < as_of
        or evidence.symbol != result.symbol
    ):
        return None
    values = {item.name: item.value for item in result.metrics}
    entry_low = _decimal(values.get("entry_zone_low"))
    entry_high = _decimal(values.get("entry_zone_high"))
    reaction_low = _decimal(values.get("recovery_reaction_low"))
    support = _decimal(values.get("support"))
    if (
        entry_low is not None
        and entry_high is not None
        and _overlaps(entry_low, entry_high, evidence.zone_low, evidence.zone_high)
    ):
        return "ENTRY_ZONE"
    if reaction_low is not None and evidence.zone_low <= reaction_low <= evidence.zone_high:
        return "RECOVERY_LOW"
    if support is not None and evidence.zone_low <= support <= evidence.zone_high:
        return "STRUCTURAL_SUPPORT"
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


def _upsert_metrics(result: AnalysisResult, *items: NamedValue) -> tuple[NamedValue, ...]:
    names = {item.name for item in items}
    return (*(item for item in result.metrics if item.name not in names), *items)


def _enriched_hash(context_hash: str, evidence: OrderFlowSupportAssessment) -> str:
    payload = f"{context_hash}|order-flow-support:{evidence.context_hash}".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"
