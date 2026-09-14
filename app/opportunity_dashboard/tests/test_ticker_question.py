import json

import httpx
import pytest

from app.opportunity_dashboard.failure_review import FailureReviewError, OpenAIFailureReviewer


async def test_question_sends_complete_snapshot_only_when_requested() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Falta confirmación."}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAIFailureReviewer(api_key="test", model="configured-gpt", client=client)
        assert requests == []
        snapshot = {"symbol": "NVDA", "assessments": [{"payload": {"entry_gate": False}}]}
        assert await reviewer.ask_ticker(snapshot, question="¿Qué falta?") == "Falta confirmación."
    assert json.loads(requests[0]["input"])["ticker_snapshot"] == snapshot
    assert requests[0]["store"] is False
    assert "tools" not in requests[0]


@pytest.mark.parametrize("body", [{"status": "incomplete"}, {"status": "completed", "output": []}])
async def test_incomplete_or_invalid_answers_are_not_successes(body: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body),
        )
    ) as client:
        reviewer = OpenAIFailureReviewer(api_key="test", model="configured-gpt", client=client)
        with pytest.raises(FailureReviewError):
            await reviewer.ask_ticker({"symbol": "NVDA"}, question="¿Gates?")
