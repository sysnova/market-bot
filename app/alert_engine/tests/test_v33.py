from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.alert_engine import AlertEngineV33, buy_maturity
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 8, 10, 22, 1, tzinfo=UTC)
HASH = "sha256:" + "9" * 64


def test_v33_turns_vlo_late_setup_into_manual_early_watch_instead_of_core_entry() -> None:
    engine = AlertEngineV33()
    swing_alert = engine.ingest(
        _swing(actionable=False, reward_risk="0.4629"), now=NOW
    )

    alert = engine.ingest(_intraday(NOW, mature=False), now=NOW)
    later = engine.ingest(
        _intraday(NOW + timedelta(minutes=3), mature=False),
        now=NOW + timedelta(minutes=3),
    )

    assert swing_alert is None
    assert alert is not None
    assert alert.kind is AlertKind.EARLY_INTRADAY_WITHOUT_CONFIRMATION
    assert alert.severity.value == "WATCH"
    assert buy_maturity(alert) is None
    assert "core_entry_not_confirmed" in alert.reasons
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["current_price"] == Decimal("315.14")
    assert metrics["swing_reward_risk_to_resistance"] == Decimal("0.4629")
    assert later is None


def test_v33_confirms_only_when_swing_asymmetry_and_intraday_mature_gate_pass() -> None:
    engine = AlertEngineV33()
    engine.ingest(_swing(actionable=True, reward_risk="2.00"), now=NOW)

    alert = engine.ingest(
        _intraday(NOW, mature=True, verdict=AnalysisVerdict.FAVORABLE, score="80"),
        now=NOW,
    )

    assert alert is not None
    assert alert.kind is AlertKind.ENTRY_CONFIRMED


def test_v33_emits_swing_setup_only_after_real_reward_risk_gate_passes() -> None:
    engine = AlertEngineV33()

    alert = engine.ingest(_swing(actionable=True, reward_risk="2.00"), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.SWING_SETUP


def _swing(*, actionable: bool, reward_risk: str) -> AnalysisResult:
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
            NamedValue(name="reference_price", value=Decimal("314.495")),
            NamedValue(name="classification", value="pullback"),
            NamedValue(name="anchored_vwap_gate_passed", value=True),
            NamedValue(name="structure_broken_confirmed", value=False),
            NamedValue(name="swing_entry_gate_passed", value=actionable),
            NamedValue(
                name="reward_risk_to_resistance", value=Decimal(reward_risk)
            ),
            NamedValue(name="resistance", value=Decimal("320.24")),
            NamedValue(name="invalidation", value=Decimal("302.0846")),
        ),
        context_hash=HASH,
    )


def _intraday(
    as_of: datetime,
    *,
    mature: bool,
    verdict: AnalysisVerdict = AnalysisVerdict.WATCH,
    score: str = "64",
) -> AnalysisResult:
    return AnalysisResult(
        engine_id="intraday",
        engine_version="4.0.0",
        symbol="VLO",
        horizon=AnalysisHorizon.INTRADAY,
        as_of=as_of,
        verdict=verdict,
        direction=PatternDirection.BULLISH,
        score=Decimal(score),
        confidence=Decimal(score) / Decimal("100"),
        reasons=("setup:bullish_breakout",),
        metrics=(
            NamedValue(name="setup", value="bullish_breakout"),
            NamedValue(name="reference_price", value=Decimal("315.14")),
            NamedValue(name="confirmation_quality", value="strong"),
            NamedValue(name="five_minute_higher_low", value=True),
            NamedValue(name="mature_confirmation_gate_passed", value=mature),
            NamedValue(name="entry_efficiency_gate_passed", value=mature),
            NamedValue(name="entry_trigger_level", value=Decimal("314.97")),
            NamedValue(name="objective_level", value=Decimal("316.3218")),
        ),
        context_hash=HASH,
    )
