from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.alert_engine import AlertEngineV37
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def _analysis(
    *, horizon: AnalysisHorizon, direction: PatternDirection, verdict: AnalysisVerdict
) -> AnalysisResult:
    metrics = (
        (
            NamedValue(name="materiality", value="HIGH"),
            NamedValue(name="expires_at", value=NOW + timedelta(hours=24)),
            NamedValue(name="article_url", value="https://example.com/risk"),
        )
        if horizon is AnalysisHorizon.NEWS
        else (
            NamedValue(name="reference_price", value=Decimal("124")),
            NamedValue(name="classification", value="pullback"),
            NamedValue(name="anchored_vwap_gate_passed", value=True),
            NamedValue(name="structure_broken_confirmed", value=False),
            NamedValue(name="swing_entry_gate_passed", value=True),
            NamedValue(name="reward_risk_to_resistance", value=Decimal("2")),
        )
    )
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        symbol="VLO",
        horizon=horizon,
        as_of=NOW,
        verdict=verdict,
        direction=direction,
        score=Decimal("90"),
        confidence=Decimal("0.90"),
        reasons=("material_news" if horizon is AnalysisHorizon.NEWS else "technical_setup",),
        metrics=metrics,
        context_hash=f"sha256:{'a' * 64 if horizon is AnalysisHorizon.NEWS else 'b' * 64}",
    )


def test_material_news_marks_but_does_not_suppress_buy_alert() -> None:
    engine = AlertEngineV37()
    warning = engine.ingest(
        _analysis(
            horizon=AnalysisHorizon.NEWS,
            direction=PatternDirection.BEARISH,
            verdict=AnalysisVerdict.AVOID,
        ),
        now=NOW,
    )
    assert warning is not None and warning.kind is AlertKind.NEWS_RISK
    assert "remain enabled" in warning.message
    assert not engine.news_blocks_entry("VLO", now=NOW)

    buy = engine.ingest(
        _analysis(
            horizon=AnalysisHorizon.SWING,
            direction=PatternDirection.BULLISH,
            verdict=AnalysisVerdict.FAVORABLE,
        ),
        now=NOW,
    )

    assert buy is not None
    assert buy.kind is AlertKind.SWING_SETUP
    assert any(item.name == "news_risk_active" and item.value is True for item in buy.metrics)
    assert AnalysisHorizon.NEWS in buy.horizons
    assert "active_material_news_risk" in buy.reasons
