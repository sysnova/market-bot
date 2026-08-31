import json
from decimal import Decimal

import httpx
import pytest

from app.opportunity_dashboard import OpenAIFailureReviewer, build_failure_dossier
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
        "confidence": "0.45",
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
    assert body["reasoning"] == {"effort": "medium"}
