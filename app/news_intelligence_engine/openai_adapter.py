"""OpenAI Responses API adapter using strict structured output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import httpx

from app.alpaca_market_data import AlpacaNewsArticle

from .models import NewsAssessmentBatch


class OpenAIResponsesError(RuntimeError):
    """Safe provider failure that never includes credentials or article text."""


class OpenAINewsClassifier:
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        if not api_key.strip() or not model.strip() or not prompt.strip():
            raise ValueError("OpenAI key, model, and prompt are required")
        self.model = model.strip()
        self._prompt = prompt.strip()
        self._headers = {"Authorization": f"Bearer {api_key.strip()}"}
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def classify(self, article: AlpacaNewsArticle) -> NewsAssessmentBatch:
        payload = {
            "model": self.model,
            "instructions": self._prompt,
            "input": json.dumps(
                {
                    "article_id": article.article_id,
                    "created_at": article.created_at.isoformat(),
                    "updated_at": article.updated_at.isoformat(),
                    "headline": article.headline,
                    "summary": article.summary,
                    "source": article.source,
                    "symbols": article.symbols,
                },
                ensure_ascii=False,
            ),
            "reasoning": {"effort": "none"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "marketbot_news_assessment",
                    "strict": True,
                    "schema": NewsAssessmentBatch.model_json_schema(),
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
            raise OpenAIResponsesError("OpenAI Responses request failed") from error
        if not 200 <= response.status_code < 300:
            raise OpenAIResponsesError(
                f"OpenAI Responses request failed with HTTP {response.status_code}"
            )
        try:
            body = response.json()
            text = _output_text(cast("Mapping[str, object]", body))
            return NewsAssessmentBatch.model_validate_json(text, strict=False)
        except (TypeError, ValueError, KeyError) as error:
            raise OpenAIResponsesError("OpenAI returned an invalid structured response") from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _output_text(body: Mapping[str, object]) -> str:
    output = body.get("output")
    if not isinstance(output, list):
        raise TypeError
    for raw_item in cast("list[object]", output):
        if not isinstance(raw_item, Mapping):
            continue
        item = cast("Mapping[str, object]", raw_item)
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for raw_part in cast("list[object]", content):
            if not isinstance(raw_part, Mapping):
                continue
            part = cast("Mapping[str, object]", raw_part)
            if part.get("type") != "output_text":
                continue
            value = part.get("text")
            if isinstance(value, str) and value:
                return value
    raise TypeError
