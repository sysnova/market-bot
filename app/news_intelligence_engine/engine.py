"""Deterministically project structured news assessments into AnalysisResult."""

from __future__ import annotations

from datetime import UTC, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.alpaca_market_data import AlpacaNewsArticle
from app.common.canonical import sha256_digest
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

from .models import (
    NewsAssessmentBatch,
    NewsDirection,
    NewsMateriality,
    NewsTickerAssessment,
)

_MATERIALITY_WEIGHT = {
    NewsMateriality.LOW: Decimal("0.25"),
    NewsMateriality.MEDIUM: Decimal("0.50"),
    NewsMateriality.HIGH: Decimal("0.80"),
    NewsMateriality.CRITICAL: Decimal("1.00"),
}


class NewsIntelligenceEngine:
    """Pure projection layer; the LLM can classify but cannot decide an order."""

    engine_id = "news-intelligence"
    engine_version = "1.0.0"

    def project(
        self,
        article: AlpacaNewsArticle,
        batch: NewsAssessmentBatch,
        *,
        model: str,
        prompt_version: str,
    ) -> tuple[AnalysisResult, ...]:
        if batch.article_id != article.article_id:
            raise ValueError("classifier article_id does not match the source article")
        allowed = set(article.symbols)
        results: list[AnalysisResult] = []
        for assessment in batch.assessments:
            if assessment.symbol not in allowed or not assessment.relevant:
                continue
            results.append(
                _result(
                    article,
                    assessment,
                    model=model,
                    prompt_version=prompt_version,
                )
            )
        return tuple(results)


def _result(
    article: AlpacaNewsArticle,
    assessment: NewsTickerAssessment,
    *,
    model: str,
    prompt_version: str,
) -> AnalysisResult:
    direction = {
        NewsDirection.BULLISH: PatternDirection.BULLISH,
        NewsDirection.BEARISH: PatternDirection.BEARISH,
        NewsDirection.NEUTRAL: PatternDirection.NEUTRAL,
        NewsDirection.MIXED: PatternDirection.NEUTRAL,
    }[assessment.direction]
    verdict = _verdict(assessment)
    strength = (
        assessment.confidence
        * assessment.relevance
        * _MATERIALITY_WEIGHT[assessment.materiality]
    )
    score = (Decimal("50") + Decimal("50") * strength).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    as_of = article.updated_at.astimezone(UTC)
    expires_at = as_of + timedelta(hours=assessment.expected_duration_hours)
    content_hash = sha256_digest(
        {
            "article_id": article.article_id,
            "headline": article.headline,
            "summary": article.summary,
            "updated_at": article.updated_at,
        }
    )
    context_hash = "sha256:" + sha256_digest(
        {
            "content_hash": content_hash,
            "symbol": assessment.symbol,
            "assessment": assessment,
            "model": model,
            "prompt_version": prompt_version,
        }
    )
    evidence = " | ".join(item.strip() for item in assessment.evidence if item.strip())
    flags = ",".join(item.strip() for item in assessment.risk_flags if item.strip())
    return AnalysisResult(
        engine_id="news-intelligence",
        engine_version="1.0.0",
        symbol=assessment.symbol,
        horizon=AnalysisHorizon.NEWS,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=score,
        confidence=assessment.confidence,
        reasons=(
            f"news_event:{assessment.event_type.value.lower()}",
            f"news_materiality:{assessment.materiality.value.lower()}",
            f"news_thesis:{assessment.thesis.strip()}",
        ),
        metrics=(
            NamedValue(name="article_id", value=article.article_id),
            NamedValue(name="article_url", value=article.url or "unavailable"),
            NamedValue(name="news_source", value=article.source or "alpaca"),
            NamedValue(name="event_type", value=assessment.event_type.value),
            NamedValue(name="materiality", value=assessment.materiality.value),
            NamedValue(name="impact_horizon", value=assessment.impact_horizon.value),
            NamedValue(name="expected_duration_hours", value=assessment.expected_duration_hours),
            NamedValue(name="expires_at", value=expires_at),
            NamedValue(name="prompt_version", value=prompt_version),
            NamedValue(name="model", value=model),
            NamedValue(name="relevance", value=assessment.relevance),
            NamedValue(name="evidence", value=evidence or "not_provided"),
            NamedValue(name="risk_flags", value=flags or "none"),
            NamedValue(name="content_hash", value=content_hash),
        ),
        context_hash=context_hash,
    )


def _verdict(assessment: NewsTickerAssessment) -> AnalysisVerdict:
    if assessment.insufficient_data:
        return AnalysisVerdict.INSUFFICIENT_DATA
    if assessment.direction is NewsDirection.BEARISH:
        if assessment.materiality in {NewsMateriality.HIGH, NewsMateriality.CRITICAL}:
            return AnalysisVerdict.AVOID
        if assessment.materiality is NewsMateriality.MEDIUM:
            return AnalysisVerdict.CAUTION
        return AnalysisVerdict.WATCH
    if assessment.direction is NewsDirection.BULLISH:
        if assessment.materiality in {NewsMateriality.HIGH, NewsMateriality.CRITICAL}:
            return AnalysisVerdict.FAVORABLE
        return AnalysisVerdict.WATCH
    return AnalysisVerdict.WATCH
