import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EntryOpportunityEvent,
    EntrySignalFamily,
    PatternDirection,
    SwingTradeMaturity,
)
from app.opportunity_dashboard import (
    FailureReviewError,
    OpenAIFailureReviewer,
    build_failure_dossier,
)
from app.opportunity_dashboard.tests.test_projection import opportunity


@pytest.mark.unit
def test_failure_dossier_requires_loss_and_preserves_evidence_boundary() -> None:
    item = opportunity()
    losing = item.checkpoints[1]

    dossier = build_failure_dossier(item, checkpoint_id=losing.checkpoint_id)

    assert dossier["symbol"] == "AAPL"
    assert dossier["selected_thesis"]["snapshot_or_final_pnl_percent"] == "-5.00"
    assert "Do not treat evidence recorded after" in dossier["evidence_rules"]["causality"]


@pytest.mark.unit
def test_failure_dossier_rejects_non_losing_checkpoint() -> None:
    item = opportunity()
    positive = item.checkpoints[0].model_copy(
        update={"current_price": item.checkpoints[0].entry_price + 1}
    )
    item = item.model_copy(update={"checkpoints": (positive, *item.checkpoints[1:])})

    with pytest.raises(ValueError, match="losing checkpoint"):
        build_failure_dossier(item, checkpoint_id=positive.checkpoint_id)


@pytest.mark.unit
def test_failure_dossier_recovers_entry_snapshot_and_does_not_backdate_later_evidence() -> None:
    item = opportunity()
    cp = item.checkpoints[1]
    entry = AnalysisResult(
        analysis_id=UUID("0195f3a5-9000-7000-8000-000000000011"),
        engine_id="intraday",
        engine_version="4.0.0",
        symbol=item.symbol,
        horizon=AnalysisHorizon.INTRADAY,
        as_of=cp.reached_at - timedelta(minutes=1),
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("100"),
        confidence=Decimal("1"),
        reasons=("confirmed",),
        metrics=(),
        context_hash="sha256:" + "a" * 64,
    )
    late = entry.model_copy(
        update={
            "analysis_id": UUID("0195f3a5-9000-7000-8000-000000000012"),
            "verdict": AnalysisVerdict.WATCH,
        }
    )
    event = EntryOpportunityEvent(
        event_id=UUID("0195f3a5-9000-7000-8000-000000000021"),
        occurred_at=cp.reached_at,
        opportunity=item.model_copy(update={"latest_analyses": (entry,)}),
        reasons=("entry_confirmed",),
    )
    current = item.model_copy(update={"latest_analyses": (entry, late)})
    dossier = build_failure_dossier(current, checkpoint_id=cp.checkpoint_id, events=(event,))
    assert dossier["entry_analysis_snapshot"][0]["verdict"] == "FAVORABLE"
    assert len(dossier["analysis_timeline"]) == 2
    late_evidence = next(
        a for a in dossier["analysis_timeline"] if a["analysis_id"] == str(late.analysis_id)
    )
    assert late_evidence["available_at_entry"] is False
    assert dossier["evidence_coverage"]["entry_snapshot_available"] is True
    missing = build_failure_dossier(current, checkpoint_id=cp.checkpoint_id)
    assert missing["entry_analysis_snapshot"] == []
    assert missing["evidence_coverage"]["entry_snapshot_available"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stage", "kind", "interpretation"),
    [
        (SwingTradeMaturity.ST1, "REFERENCE", "REFERENCE_MOVEMENT"),
        (SwingTradeMaturity.ST3, "BUY", "CONFIRMED_ENTRY_RETURN"),
    ],
)
def test_failure_dossier_distinguishes_tracking_from_confirmed_entry(
    stage: SwingTradeMaturity, kind: str, interpretation: str
) -> None:
    item = opportunity()
    checkpoint = item.checkpoints[1].model_copy(
        update={
            "signal_family": EntrySignalFamily.SWING_TRADE,
            "swing_trade_maturity": stage,
        }
    )
    item = item.model_copy(update={"checkpoints": (checkpoint,)})
    dossier = build_failure_dossier(item, checkpoint_id=checkpoint.checkpoint_id)
    assert dossier["selected_thesis"]["entry_kind"] == kind
    assert dossier["selected_thesis"]["pnl_interpretation"] == interpretation
    assert dossier["all_checkpoints"][0]["entry_kind"] == kind


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_review_uses_strict_structured_output() -> None:
    observed: dict[str, object] = {}
    result = {
        "summary": "La confirmación falló antes de la invalidación.",
        "invalidation_patterns": [],
        "expected_but_missing": [],
        "order_flow_failure": [],
        "early_warning_signals": [],
        "protection_candidates": [],
        "data_gaps": ["No hay estados de order flow persistidos."],
        "confidence": 0.45,
        "requires_backtest": True,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(result)}],
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reviewer = OpenAIFailureReviewer(api_key="secret", model="model", client=client)
    parsed = await reviewer.review({"symbol": "AAPL"})
    await client.aclose()

    assert parsed.confidence == Decimal("0.45")
    body = observed["body"]
    assert isinstance(body, dict)
    assert body["text"]["format"]["strict"] is True
    schema = body["text"]["format"]["schema"]
    for node in (schema, *schema.get("$defs", {}).values()):
        assert set(node["required"]) == set(node["properties"])
        assert node["additionalProperties"] is False
    confidence = schema["properties"]["confidence"]
    assert confidence["type"] == "number"
    assert confidence["minimum"] == 0
    assert confidence["maximum"] == 1
    assert "anyOf" not in confidence
    assert body["reasoning"] == {"effort": "medium"}
    assert body["max_output_tokens"] == 8192


@pytest.mark.asyncio
async def test_review_schema_error_does_not_echo_private_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "invalid_json_schema", "message": "secret private dossier"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAIFailureReviewer(api_key="private-key", model="model", client=client)
        with pytest.raises(FailureReviewError) as caught:
            await reviewer.review({"private": "dossier"})

    assert "HTTP 400 (invalid_json_schema)" in str(caught.value)
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_incomplete_review_reports_token_limit_instead_of_a_json_parse_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAIFailureReviewer(api_key="secret", model="model", client=client)
        with pytest.raises(FailureReviewError, match=r"incomplete.*max_output_tokens"):
            await reviewer.review({"symbol": "SYNTHETIC"})
