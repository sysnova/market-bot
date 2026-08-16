from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.alert_engine import AlertEngineV36
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def news(*, direction: PatternDirection, verdict: AnalysisVerdict) -> AnalysisResult:
    return AnalysisResult(
        engine_id="news-intelligence",
        engine_version="1.0.0",
        symbol="VLO",
        horizon=AnalysisHorizon.NEWS,
        as_of=NOW,
        verdict=verdict,
        direction=direction,
        score=Decimal("90"),
        confidence=Decimal("0.90"),
        reasons=("news_event:regulatory",),
        metrics=(
            NamedValue(name="materiality", value="HIGH"),
            NamedValue(name="expires_at", value=NOW + timedelta(hours=24)),
        ),
        context_hash=f"sha256:{'a' * 64}",
    )


def swing() -> AnalysisResult:
    return AnalysisResult(
        engine_id="swing",
        engine_version="5.0.0",
        symbol="VLO",
        horizon=AnalysisHorizon.SWING,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("87"),
        confidence=Decimal("0.87"),
        reasons=("bullish_daily_trend",),
        metrics=(NamedValue(name="reference_price", value=Decimal("124")),),
        context_hash=f"sha256:{'b' * 64}",
    )


def test_bearish_material_news_warns_and_blocks_new_buy_alerts() -> None:
    engine = AlertEngineV36()
    warning = engine.ingest(
        news(direction=PatternDirection.BEARISH, verdict=AnalysisVerdict.AVOID), now=NOW
    )
    assert warning is not None
    assert warning.kind is AlertKind.NEWS_RISK
    assert engine.news_blocks_entry("VLO", now=NOW)
    assert engine.ingest(swing(), now=NOW) is None


def test_bullish_news_does_not_emit_an_alert_by_itself() -> None:
    engine = AlertEngineV36()
    assert engine.ingest(
        news(direction=PatternDirection.BULLISH, verdict=AnalysisVerdict.FAVORABLE), now=NOW
    ) is None
    assert not engine.news_blocks_entry("VLO", now=NOW)
