from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.alert_engine import AlertEngineV39
from app.alert_engine.confirmed import is_audible_alert, is_portfolio_monitor_alert
from app.alert_engine.formatter import format_local_alert
from app.contracts import (
    AlertKind,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)

NOW = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
HASH = "sha256:" + "9" * 64


def _result(
    horizon: AnalysisHorizon,
    *,
    direction: PatternDirection,
    verdict: AnalysisVerdict,
    metrics: tuple[NamedValue, ...],
    as_of: datetime = NOW - timedelta(minutes=1),
) -> AnalysisResult:
    return AnalysisResult(
        engine_id=horizon.value.lower(),
        engine_version="1.0.0",
        symbol="ASTS",
        horizon=horizon,
        as_of=as_of,
        verdict=verdict,
        direction=direction,
        score=Decimal("80"),
        confidence=Decimal("0.80"),
        reasons=("fixture",),
        metrics=metrics,
        context_hash=HASH,
    )


def _swing(*, gate: bool = True) -> AnalysisResult:
    return _result(
        AnalysisHorizon.SWING,
        direction=PatternDirection.BEARISH,
        verdict=AnalysisVerdict.AVOID,
        metrics=(
            NamedValue(name="reference_price", value=Decimal("60")),
            NamedValue(name="reward_risk_to_resistance", value=Decimal("0")),
            NamedValue(name="short_structure_gate_passed", value=gate),
            NamedValue(name="short_setup_id", value="swing-short:ASTS:2026-08-12"),
        ),
    )


def _intraday(*, gate: bool = True) -> AnalysisResult:
    return _result(
        AnalysisHorizon.INTRADAY,
        direction=PatternDirection.BEARISH,
        verdict=AnalysisVerdict.FAVORABLE,
        metrics=(
            NamedValue(name="setup", value="bearish_breakdown"),
            NamedValue(name="reference_price", value=Decimal("58")),
            NamedValue(name="invalidation_level", value=Decimal("59.20")),
            NamedValue(name="objective_level", value=Decimal("56.20")),
            NamedValue(name="short_mature_confirmation_gate_passed", value=gate),
            NamedValue(name="short_confirmation_rule_version", value="1.1.0"),
        ),
    )


def test_v39_emits_short_confirmed_from_broken_swing_thesis_and_intraday() -> None:
    engine = AlertEngineV39()
    assert engine.ingest(_swing(), now=NOW) is None

    alert = engine.ingest(_intraday(), now=NOW)

    assert alert is not None
    assert alert.kind is AlertKind.BEARISH_CONSENSUS
    assert alert.title == "ASTS SHORT CONFIRMED"
    metrics = {item.name: item.value for item in alert.metrics}
    assert metrics["short_entry_price"] == Decimal("58")
    assert metrics["short_invalidation"] == Decimal("59.20")
    assert metrics["short_target"] == Decimal("56.20")
    assert metrics["short_setup_id"] == "swing-short:ASTS:2026-08-12"
    assert "short_entry_confirmed" in alert.reasons


def test_v39_requires_both_short_gates() -> None:
    engine = AlertEngineV39()
    assert engine.ingest(_swing(gate=False), now=NOW) is None
    assert engine.ingest(_intraday(), now=NOW) is None


def test_confirmed_short_renders_its_own_levels_and_is_audible() -> None:
    engine = AlertEngineV39()
    swing = _swing()
    swing = swing.model_copy(
        update={
            "metrics": (
                *swing.metrics,
                NamedValue(name="invalidation", value=Decimal("54.8645")),
                NamedValue(name="target_2r", value=Decimal("75.4760")),
                NamedValue(name="buy_zone_low", value=Decimal("62")),
                NamedValue(name="buy_zone_high", value=Decimal("65")),
            )
        }
    )
    engine.ingest(swing, now=NOW)
    alert = engine.ingest(_intraday(), now=NOW)
    assert alert is not None
    text = format_local_alert(alert)
    assert "Invalidation $59.2" in text
    assert "Objective $56.2" in text
    assert "Invalidation $54.8645" not in text
    assert "Objective $75.4760" not in text
    assert "Buy zone" not in text
    assert is_audible_alert(alert)
    assert is_portfolio_monitor_alert(alert)
    unconfirmed = alert.model_copy(update={"reasons": ("bearish_consensus",)})
    assert not is_audible_alert(unconfirmed)
    assert not is_portfolio_monitor_alert(unconfirmed)
