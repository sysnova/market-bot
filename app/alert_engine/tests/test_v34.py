from datetime import UTC, datetime
from decimal import Decimal

from app.alert_engine import AlertEngineV34
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 8, 11, 20, tzinfo=UTC)
HASH = "sha256:" + "d" * 64


def test_v34_emits_human_watch_for_confirmed_obv_divergence() -> None:
    alert = AlertEngineV34().ingest(_volume_structure(), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.OBV_BULLISH_DIVERGENCE
    assert alert.severity.value == "WATCH"
    assert alert.score == Decimal("82")
    assert alert.component_analyses[0].horizon is AnalysisHorizon.VOLUME_STRUCTURE


def test_v34_adds_bounded_obv_boost_without_bypassing_entry_gates() -> None:
    engine = AlertEngineV34()
    engine.ingest(_volume_structure(), now=NOW)

    alert = engine.ingest(_swing(), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.SWING_SETUP
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["base_score"] == Decimal("75.69")
    assert metrics["volume_structure_boost"] == Decimal("10")
    assert metrics["effective_score"] == Decimal("85.69")
    assert alert.score == Decimal("85.69")
    assert "volume_structure_boost:+10" in alert.reasons


def _volume_structure() -> AnalysisResult:
    return AnalysisResult(
        engine_id="volume-structure",
        engine_version="1.0.0",
        symbol="VLO",
        horizon=AnalysisHorizon.VOLUME_STRUCTURE,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("82"),
        confidence=Decimal("0.82"),
        reasons=("weekly_obv_bullish_divergence",),
        metrics=(
            NamedValue(name="divergence_state", value="RECLAIM_CONFIRMED"),
            NamedValue(name="evidence_boost", value=Decimal("10")),
            NamedValue(name="reclaim_trigger", value=Decimal("126.30")),
            NamedValue(name="invalidation", value=Decimal("119.80")),
        ),
        context_hash=HASH,
    )


def _swing() -> AnalysisResult:
    return AnalysisResult(
        engine_id="swing",
        engine_version="4.0.0",
        symbol="VLO",
        horizon=AnalysisHorizon.SWING,
        as_of=NOW,
        verdict=AnalysisVerdict.FAVORABLE,
        direction=PatternDirection.BULLISH,
        score=Decimal("87"),
        confidence=Decimal("0.87"),
        reasons=("bullish_daily_trend",),
        metrics=(
            NamedValue(name="reference_price", value=Decimal("124")),
            NamedValue(name="classification", value="pullback"),
            NamedValue(name="anchored_vwap_gate_passed", value=True),
            NamedValue(name="structure_broken_confirmed", value=False),
            NamedValue(name="swing_entry_gate_passed", value=True),
            NamedValue(name="reward_risk_to_resistance", value=Decimal("2")),
        ),
        context_hash="sha256:" + "e" * 64,
    )
