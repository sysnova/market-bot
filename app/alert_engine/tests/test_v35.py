from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.alert_engine import AlertEngineV35
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    GammaAssessment,
    NamedValue,
    PatternDirection,
)
from app.options_gamma_engine import gamma_analysis_from_assessment

NOW = datetime(2026, 8, 12, 15, tzinfo=UTC)


def gamma(*, expires_at: datetime) -> GammaAssessment:
    return GammaAssessment(
        symbol="VLO",
        generated_at=NOW - timedelta(minutes=5),
        expires_at=expires_at,
        engine_version="1.0.0",
        methodology_version="1.0.0",
        spot_price=Decimal("124"),
        spot_as_of=NOW - timedelta(minutes=5),
        expiration_from=date(2026, 8, 12),
        expiration_to=date(2026, 9, 25),
        open_interest_as_of=date(2026, 8, 11),
        status="AVAILABLE",
        quality_score=Decimal("95"),
        contract_count=100,
        usable_contract_count=100,
        coverage_ratio=Decimal("1"),
        gamma_regime="POSITIVE",
        directional_bias="NEUTRAL",
        net_gamma_exposure=Decimal("500000"),
        absolute_gamma_exposure=Decimal("1000000"),
        net_gamma_ratio=Decimal("0.5"),
        call_wall=Decimal("124.50"),
        put_wall=Decimal("118"),
        absolute_gamma_wall=Decimal("124.50"),
        max_pain=Decimal("123"),
        gamma_flip=Decimal("120"),
        expected_move_low=Decimal("116"),
        expected_move_high=Decimal("132"),
        pin_risk=False,
        acceleration_risk=False,
        dealer_sign_assumption="CALL_POSITIVE_PUT_NEGATIVE",
        context_hash=f"sha256:{'a' * 64}",
    )


def swing() -> AnalysisResult:
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
        context_hash=f"sha256:{'b' * 64}",
    )


def test_v35_penalizes_nearby_call_wall_without_changing_alert_kind() -> None:
    engine = AlertEngineV35()
    engine.ingest(
        gamma_analysis_from_assessment(
            gamma(expires_at=NOW + timedelta(minutes=15))
        ),
        now=NOW,
    )

    alert = engine.ingest(swing(), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.SWING_SETUP
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["gamma_score_delta"] == Decimal("-6")
    assert metrics["gamma_call_wall"] == Decimal("124.50")
    assert alert.score == Decimal("69.69")
    assert "options_gamma_call_wall_limits_upside:-6" in alert.reasons


def test_v35_ignores_expired_gamma_context() -> None:
    engine = AlertEngineV35()
    engine.ingest(
        gamma_analysis_from_assessment(
            gamma(expires_at=NOW - timedelta(minutes=1))
        ),
        now=NOW,
    )

    alert = engine.ingest(swing(), now=NOW)

    assert alert is not None
    assert all(item.name != "gamma_score_delta" for item in alert.metrics)
