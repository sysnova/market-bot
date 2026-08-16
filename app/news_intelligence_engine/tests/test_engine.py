from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.alpaca_market_data import AlpacaNewsArticle
from app.contracts import AnalysisHorizon, AnalysisVerdict, PatternDirection
from app.news_intelligence_engine import (
    NewsAssessmentBatch,
    NewsDirection,
    NewsEventType,
    NewsImpactHorizon,
    NewsIntelligenceEngine,
    NewsMateriality,
    NewsTickerAssessment,
)


def _article() -> AlpacaNewsArticle:
    at = datetime(2026, 8, 16, 12, tzinfo=UTC)
    return AlpacaNewsArticle(
        article_id=42,
        headline="Company announces registered offering",
        summary="The company filed a new common-stock offering.",
        author="wire",
        created_at=at,
        updated_at=at,
        url="https://example.com/42",
        symbols=("ABC", "XYZ"),
        source="wire",
    )


@pytest.mark.unit
def test_projects_relevant_bearish_news_deterministically() -> None:
    assessment = NewsTickerAssessment(
        symbol="abc",
        relevant=True,
        event_type=NewsEventType.DILUTION,
        direction=NewsDirection.BEARISH,
        materiality=NewsMateriality.HIGH,
        confidence=Decimal("0.90"),
        relevance=Decimal("1"),
        impact_horizon=NewsImpactHorizon.SWING,
        expected_duration_hours=48,
        thesis="Potential dilution increases supply.",
        evidence=("registered offering",),
        risk_flags=("dilution",),
    )
    batch = NewsAssessmentBatch(article_id=42, assessments=(assessment,))

    result = NewsIntelligenceEngine().project(
        _article(), batch, model="gpt-5.4-nano-2026-03-17", prompt_version="1.0.0"
    )[0]

    assert result.horizon is AnalysisHorizon.NEWS
    assert result.direction is PatternDirection.BEARISH
    assert result.verdict is AnalysisVerdict.AVOID
    assert result.score == Decimal("86.00")
    assert next(metric.value for metric in result.metrics if metric.name == "expires_at") == (
        datetime(2026, 8, 18, 12, tzinfo=UTC)
    )


@pytest.mark.unit
def test_ignores_irrelevant_or_unrelated_tickers() -> None:
    irrelevant = NewsTickerAssessment(
        symbol="ABC",
        relevant=False,
        event_type=NewsEventType.OTHER,
        direction=NewsDirection.NEUTRAL,
        materiality=NewsMateriality.LOW,
        confidence=Decimal("0.5"),
        relevance=Decimal("0.1"),
        impact_horizon=NewsImpactHorizon.INTRADAY,
        expected_duration_hours=1,
        thesis="No material relationship.",
    )
    batch = NewsAssessmentBatch(article_id=42, assessments=(irrelevant,))
    assert NewsIntelligenceEngine().project(
        _article(), batch, model="model", prompt_version="1.0.0"
    ) == ()


@pytest.mark.unit
def test_rejects_classifier_article_mismatch() -> None:
    with pytest.raises(ValueError, match="article_id"):
        NewsIntelligenceEngine().project(
            _article(),
            NewsAssessmentBatch(article_id=99, assessments=()),
            model="model",
            prompt_version="1.0.0",
        )
