"""Evidence-bounded OpenAI review of a losing paper thesis."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import cast
from uuid import UUID

import httpx
from pydantic import Field

from app.contracts import EntryMaturityCheckpoint, EntryOpportunity, EntryOpportunityEvent
from app.contracts._base import StrictFrozenModel

from .projection import checkpoint_pnl_percent


class FailureFinding(StrictFrozenModel):
    pattern: str = Field(min_length=1, max_length=240)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=6)
    interpretation: str = Field(min_length=1, max_length=500)
    timing: str = Field(min_length=1, max_length=160)


class ProtectionCandidate(StrictFrozenModel):
    signal: str = Field(min_length=1, max_length=240)
    rationale: str = Field(min_length=1, max_length=500)
    test: str = Field(min_length=1, max_length=500)
    risk_of_false_positive: str = Field(min_length=1, max_length=300)


class FailureReview(StrictFrozenModel):
    summary: str = Field(min_length=1, max_length=800)
    invalidation_patterns: tuple[FailureFinding, ...] = Field(max_length=8)
    expected_but_missing: tuple[FailureFinding, ...] = Field(max_length=8)
    order_flow_failure: tuple[FailureFinding, ...] = Field(max_length=8)
    early_warning_signals: tuple[FailureFinding, ...] = Field(max_length=8)
    protection_candidates: tuple[ProtectionCandidate, ...] = Field(max_length=8)
    data_gaps: tuple[str, ...] = Field(max_length=12)
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    requires_backtest: bool = True


class FailureReviewError(RuntimeError):
    """Safe review failure that never includes credentials or full provider responses."""


class OpenAIFailureReviewer:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ValueError("OpenAI key and model are required")
        self.model = model.strip()
        self._headers = {"Authorization": f"Bearer {api_key.strip()}"}
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def review(self, dossier: Mapping[str, object], *, user_notes: str = "") -> FailureReview:
        payload = {
            "model": self.model,
            "instructions": _PROMPT,
            "input": json.dumps(
                {"dossier": dossier, "operator_notes": user_notes.strip()[:2000]},
                ensure_ascii=False,
            ),
            "reasoning": {"effort": "medium"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "marketbot_failure_review",
                    "strict": True,
                    "schema": FailureReview.model_json_schema(),
                }
            },
        }
        try:
            response = await self._client.post(
                self.endpoint,
                headers=self._headers,
                json=payload,
            )
        except httpx.HTTPError as error:
            raise FailureReviewError("OpenAI failure-review request failed") from error
        if not 200 <= response.status_code < 300:
            raise FailureReviewError(
                f"OpenAI failure-review request failed with HTTP {response.status_code}"
            )
        try:
            body = cast("Mapping[str, object]", response.json())
            return FailureReview.model_validate_json(_output_text(body), strict=False)
        except (TypeError, ValueError, KeyError) as error:
            raise FailureReviewError(
                "OpenAI returned an invalid structured failure review"
            ) from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def build_failure_dossier(
    opportunity: EntryOpportunity,
    *,
    checkpoint_id: UUID,
    events: tuple[EntryOpportunityEvent, ...] = (),
) -> dict[str, object]:
    checkpoint = next(
        (item for item in opportunity.checkpoints if item.checkpoint_id == checkpoint_id),
        None,
    )
    if checkpoint is None:
        raise ValueError("checkpoint does not belong to opportunity")
    pnl = checkpoint_pnl_percent(checkpoint)
    if pnl >= 0:
        raise ValueError("failure review requires a currently or finally losing checkpoint")
    return {
        "symbol": opportunity.symbol,
        "opportunity_id": str(opportunity.opportunity_id),
        "lifecycle": {
            "status": opportunity.status.value,
            "current_maturity": opportunity.current_maturity.value,
            "peak_maturity": opportunity.peak_maturity.value,
            "armed_at": opportunity.armed_at.isoformat(),
            "updated_at": opportunity.updated_at.isoformat(),
            "closed_at": opportunity.closed_at.isoformat() if opportunity.closed_at else None,
            "close_reason": (
                opportunity.close_reason.value if opportunity.close_reason is not None else None
            ),
        },
        "selected_thesis": _checkpoint_evidence(checkpoint),
        "all_checkpoints": [_checkpoint_evidence(item) for item in opportunity.checkpoints],
        "horizon_legs": [item.model_dump(mode="json") for item in opportunity.legs],
        "signal_references": [
            item.model_dump(mode="json") for item in opportunity.signal_references
        ],
        "analysis_timeline": [
            {
                "engine": item.engine_id,
                "version": item.engine_version,
                "horizon": item.horizon.value,
                "as_of": item.as_of.isoformat(),
                "verdict": item.verdict.value,
                "direction": item.direction.value,
                "score": str(item.score),
                "confidence": str(item.confidence),
                "reasons": list(item.reasons),
                "metrics": [metric.model_dump(mode="json") for metric in item.metrics],
            }
            for item in sorted(opportunity.latest_analyses, key=lambda value: value.as_of)
        ],
        "lifecycle_events": [
            {
                "occurred_at": event.occurred_at.isoformat(),
                "reasons": list(event.reasons),
                "revision": event.opportunity.revision,
                "status": event.opportunity.status.value,
                "price": str(event.opportunity.current_price),
            }
            for event in events
        ],
        "evidence_rules": {
            "order_flow": (
                "Use only explicit order-flow metrics/reasons present in analysis_timeline or "
                "lifecycle_events. Otherwise record the missing data in data_gaps."
            ),
            "causality": "Do not treat evidence recorded after the failure as an early warning.",
            "learning": (
                "All proposed protections are hypotheses requiring out-of-sample backtests."
            ),
        },
    }


def _checkpoint_evidence(checkpoint: EntryMaturityCheckpoint) -> dict[str, object]:
    return {
        **checkpoint.model_dump(mode="json"),
        "snapshot_or_final_pnl_percent": str(checkpoint_pnl_percent(checkpoint)),
    }


def _output_text(body: Mapping[str, object]) -> str:
    output = body.get("output")
    if not isinstance(output, list):
        raise TypeError
    for raw_item in cast("list[object]", output):
        if not isinstance(raw_item, Mapping):
            continue
        item = cast("Mapping[str, object]", raw_item)
        if item.get("type") != "message" or not isinstance(item.get("content"), list):
            continue
        for raw_part in cast("list[object]", item["content"]):
            if not isinstance(raw_part, Mapping):
                continue
            part = cast("Mapping[str, object]", raw_part)
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                return cast(str, part["text"])
    raise TypeError


_PROMPT = """
Actúas como auditor post-trade de MarketBot. Responde en español y usa exclusivamente la evidencia
estructurada del dossier. Separa hechos de interpretación. No inventes DOM, tape, bid/ask, delta,
CVD, absorción ni divergencias si no aparecen en la evidencia. Identifica: qué invalidó la tesis,
qué confirmación positiva se esperaba y nunca llegó, cómo se comportó el order flow cuando exista,
y qué señales habrían protegido antes la decisión. Cada protección es una hipótesis de
investigación, no una nueva regla ni una recomendación de trading; describe el backtest y el
riesgo de falso positivo. Si la evidencia temporal o de order flow es insuficiente, decláralo en
data_gaps y reduce confidence.
""".strip()
