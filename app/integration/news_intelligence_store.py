"""Durable deduplication ledger for paid news classifications."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.contracts import AnalysisResult
from app.news_intelligence_engine import NewsAssessmentBatch


class NewsIntelligenceStoreError(RuntimeError):
    """Raised when the classification ledger cannot be read or written."""


class PostgresNewsIntelligenceStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def is_current(self, article_id: int, content_hash: str) -> bool:
        try:
            async with self._engine.connect() as connection:
                stored = await connection.scalar(
                    text(
                        """
                        select content_hash
                        from market_bot.news_intelligence_results
                        where provider = 'alpaca' and article_id = :article_id
                        """
                    ),
                    {"article_id": article_id},
                )
        except SQLAlchemyError as error:
            raise NewsIntelligenceStoreError("news ledger read failed") from error
        return stored == content_hash

    async def save(
        self,
        *,
        article_id: int,
        content_hash: str,
        article_updated_at: datetime,
        assessed_at: datetime,
        model: str,
        prompt_version: str,
        assessment: NewsAssessmentBatch,
        analysis_results: tuple[AnalysisResult, ...],
    ) -> None:
        values = {
            "article_id": article_id,
            "content_hash": content_hash,
            "article_updated_at": article_updated_at,
            "assessed_at": assessed_at,
            "model": model,
            "prompt_version": prompt_version,
            "assessment": json.dumps(assessment.model_dump(mode="json")),
            "analysis_results": json.dumps(
                [result.model_dump(mode="json") for result in analysis_results]
            ),
        }
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        insert into market_bot.news_intelligence_results (
                          provider, article_id, content_hash, article_updated_at,
                          assessed_at, model, prompt_version, assessment, analysis_results
                        ) values (
                          'alpaca', :article_id, :content_hash, :article_updated_at,
                          :assessed_at, :model, :prompt_version,
                          cast(:assessment as jsonb), cast(:analysis_results as jsonb)
                        )
                        on conflict (provider, article_id) do update set
                          content_hash = excluded.content_hash,
                          article_updated_at = excluded.article_updated_at,
                          assessed_at = excluded.assessed_at,
                          model = excluded.model,
                          prompt_version = excluded.prompt_version,
                          assessment = excluded.assessment,
                          analysis_results = excluded.analysis_results
                        """
                    ),
                    values,
                )
        except SQLAlchemyError as error:
            raise NewsIntelligenceStoreError("news ledger write failed") from error

    async def load_latest_active(self, *, now: datetime) -> tuple[AnalysisResult, ...]:
        """Restore the latest non-expired NEWS result per ticker from PostgreSQL."""

        cutoff = now - timedelta(hours=720)
        try:
            async with self._engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            """
                            select analysis_results
                            from market_bot.news_intelligence_results
                            where article_updated_at >= :cutoff
                            order by article_updated_at asc, article_id asc
                            """
                        ),
                        {"cutoff": cutoff},
                    )
                ).all()
        except SQLAlchemyError as error:
            raise NewsIntelligenceStoreError("news bootstrap read failed") from error
        payloads = (cast("object", row.analysis_results) for row in rows)
        return _latest_active_results(payloads, now=now)


def _latest_active_results(
    payloads: Iterable[object], *, now: datetime
) -> tuple[AnalysisResult, ...]:
    latest: dict[str, AnalysisResult] = {}
    for payload in payloads:
        values = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(values, list):
            continue
        for raw_result in cast("list[object]", values):
            if not isinstance(raw_result, Mapping):
                continue
            normalized = _restore_metric_datetimes(cast("Mapping[str, object]", raw_result))
            result = AnalysisResult.model_validate(normalized, strict=False)
            expires_at = _result_expiry(result)
            if expires_at is None or expires_at <= now:
                continue
            existing = latest.get(result.symbol)
            if existing is None or (result.as_of, result.analysis_id.int) > (
                existing.as_of,
                existing.analysis_id.int,
            ):
                latest[result.symbol] = result
    return tuple(latest[symbol] for symbol in sorted(latest))


def _restore_metric_datetimes(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    raw_metrics = normalized.get("metrics")
    if not isinstance(raw_metrics, list):
        return normalized
    metrics: list[object] = []
    for raw_metric in cast("list[object]", raw_metrics):
        if not isinstance(raw_metric, Mapping):
            metrics.append(raw_metric)
            continue
        metric = dict(cast("Mapping[str, object]", raw_metric))
        if metric.get("name") == "expires_at" and isinstance(metric.get("value"), str):
            value = cast("str", metric["value"])
            metric["value"] = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        metrics.append(metric)
    normalized["metrics"] = metrics
    return normalized


def _result_expiry(result: AnalysisResult) -> datetime | None:
    value = next((item.value for item in result.metrics if item.name == "expires_at"), None)
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    return None
