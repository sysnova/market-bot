import json

import httpx
import pytest

from app.opportunity_dashboard.failure_review import FailureReviewError, OpenAIFailureReviewer
from app.opportunity_dashboard.tests.test_short_context import NOW, asts_book


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


async def test_asts_question_sends_short_context_and_scoped_instructions() -> None:
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
                        "content": [{"type": "output_text", "text": "Respuesta"}],
                    }
                ],
            },
        )

    data = asts_book().snapshot(now=NOW)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAIFailureReviewer(api_key="test", model="configured-gpt", client=client)
        await reviewer.ask_ticker(data, question="¿Por qué no es SHORT si cae en el día?")
    sent = json.loads(requests[0]["input"])["ticker_snapshot"]
    assert sent["short_context"] == data["short_context"]
    instructions = str(requests[0]["instructions"])
    assert "NO significa que el SHORT esté roto" in instructions
    assert "4HGERI.short_eligible pertenece a otra tesis" in instructions
    assert "NO contiene el historial intradía completo" in instructions
    assert "máximo de 180 palabras" in instructions
    assert "son alternativas" in instructions
    assert "No agregues como motivos" in instructions
