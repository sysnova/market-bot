import json
from datetime import UTC, datetime

import httpx
import pytest

from app.alpaca_market_data import AlpacaNewsArticle
from app.news_intelligence_engine import OpenAINewsClassifier, OpenAIResponsesError


def _article() -> AlpacaNewsArticle:
    at = datetime(2026, 8, 16, 12, tzinfo=UTC)
    return AlpacaNewsArticle(
        article_id=42,
        headline="Headline",
        summary="Summary",
        author="wire",
        created_at=at,
        updated_at=at,
        url="https://example.com",
        symbols=("ABC",),
        source="wire",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_uses_responses_structured_output_and_parses_result() -> None:
    observed: dict[str, object] = {}
    result = {
        "article_id": 42,
        "assessments": [
            {
                "symbol": "ABC",
                "relevant": True,
                "event_type": "GUIDANCE",
                "direction": "BULLISH",
                "materiality": "MEDIUM",
                "confidence": 0.8,
                "relevance": 0.9,
                "impact_horizon": "SWING",
                "expected_duration_hours": 24,
                "thesis": "Guidance improved.",
                "evidence": ["raised guidance"],
                "risk_flags": [],
                "insufficient_data": False,
            }
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["Authorization"]
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
    classifier = OpenAINewsClassifier(
        api_key="secret", model="gpt-5.4-nano-2026-03-17", prompt="classify", client=client
    )
    parsed = await classifier.classify(_article())
    await client.aclose()

    assert parsed.article_id == 42
    assert observed["authorization"] == "Bearer secret"
    body = observed["body"]
    assert isinstance(body, dict)
    assert body["text"]["format"]["strict"] is True
    assert body["reasoning"] == {"effort": "none"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_error_does_not_echo_response_or_key() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret provider response")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    classifier = OpenAINewsClassifier(
        api_key="top-secret", model="model", prompt="prompt", client=client
    )
    with pytest.raises(OpenAIResponsesError) as captured:
        await classifier.classify(_article())
    await client.aclose()
    assert "top-secret" not in str(captured.value)
    assert "secret provider response" not in str(captured.value)
