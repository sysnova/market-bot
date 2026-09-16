from datetime import timedelta
from decimal import Decimal

import pytest

from app.alert_engine import AlertEngineV39
from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)
from app.intraday_engine import IntradayEngineV6, IntradayEngineV7

from .test_v6 import _ema20_extended_local_breakdown, _metric


def test_v7_confirms_strong_displacement_short_beyond_local_trigger_window() -> None:
    context = _ema20_extended_local_breakdown(final_move="-0.50")

    previous = IntradayEngineV6().analyze(context)
    result = IntradayEngineV7().analyze(context)

    assert _metric(previous, "short_entry_efficiency_gate_passed") is False
    assert result.engine_version == "7.0.0"
    assert result.direction is PatternDirection.BEARISH
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert _metric(result, "short_entry_efficiency_gate_passed") is False
    assert _metric(result, "short_displacement_gate_passed") is True
    assert _metric(result, "short_extension_override_applied") is True
    assert _metric(result, "short_mature_confirmation_gate_passed") is True
    assert _metric(result, "short_entry_timing") == "confirmed_displacement"
    assert "short_displacement_confirmed" in result.reasons
    assert "short_late_entry_wait_retest" not in result.reasons


def test_v7_displacement_does_not_require_mature_retest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = IntradayEngineV6().analyze(_ema20_extended_local_breakdown(final_move="-0.50"))
    metrics = {item.name: item.value for item in base.metrics}
    metrics.update(
        short_mature_retest_confirmed=False,
        short_mature_confirmation_gate_passed=False,
        mature_confirmation_gate_passed=False,
    )
    waiting = base.model_copy(
        update={
            "verdict": AnalysisVerdict.WATCH,
            "reasons": (*base.reasons, "short_late_entry_wait_retest"),
            "metrics": tuple(NamedValue(name=name, value=value) for name, value in metrics.items()),
        }
    )
    monkeypatch.setattr(IntradayEngineV6, "analyze", lambda *a, **kw: waiting)

    result = IntradayEngineV7().analyze(_ema20_extended_local_breakdown(final_move="-0.50"))

    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert _metric(result, "short_displacement_gate_passed") is True
    assert _metric(result, "short_mature_retest_confirmed") is False
    assert _metric(result, "short_mature_confirmation_gate_passed") is True
    assert "short_displacement_without_retest_confirmed" in result.reasons
    assert "short_late_entry_wait_retest" not in result.reasons


def test_v7_displacement_result_emits_short_confirmed_with_swing_gate() -> None:
    intraday = IntradayEngineV7().analyze(
        _ema20_extended_local_breakdown(final_move="-0.50")
    )
    swing = AnalysisResult(
        engine_id="swing",
        engine_version="14.0.0",
        symbol="ASTS",
        horizon=AnalysisHorizon.SWING,
        as_of=intraday.as_of,
        verdict=AnalysisVerdict.AVOID,
        direction=PatternDirection.BEARISH,
        score=Decimal("14"),
        confidence=Decimal("0.14"),
        reasons=("failed_breakout_short_thesis_broken",),
        metrics=(
            NamedValue(name="reference_price", value=Decimal("59.20")),
            NamedValue(name="reward_risk_to_resistance", value=Decimal("0")),
            NamedValue(name="short_structure_gate_passed", value=True),
            NamedValue(
                name="short_setup_id",
                value="swing-short:ASTS:2026-08-12",
            ),
        ),
        context_hash="sha256:" + "7" * 64,
    )
    alert_engine = AlertEngineV39()
    now = intraday.as_of + timedelta(minutes=1)

    assert alert_engine.ingest(swing, now=now) is None
    alert = alert_engine.ingest(intraday, now=now)

    assert alert is not None
    assert alert.title == "ASTS SHORT CONFIRMED"
    assert "short_entry_confirmed" in alert.reasons


def test_v7_keeps_extended_short_blocked_without_abrupt_momentum() -> None:
    result = IntradayEngineV7(
        short_displacement_minimum_momentum_percent=Decimal("0.50"),
        short_displacement_minimum_rvol=Decimal("2.00"),
    ).analyze(_ema20_extended_local_breakdown(final_move="-0.25"))

    assert result.verdict is AnalysisVerdict.WATCH
    assert _metric(result, "short_displacement_gate_passed") is False
    assert _metric(result, "short_mature_confirmation_gate_passed") is False
