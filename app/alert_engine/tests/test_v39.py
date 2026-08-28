from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.alert_engine import AlertEngineV39
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
