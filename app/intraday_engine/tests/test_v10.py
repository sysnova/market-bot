# pyright: reportPrivateUsage=false
from decimal import Decimal

import pytest

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue
from app.intraday_engine.tests.test_v5 import _efficient_bearish_context
from app.intraday_engine.tests.test_v8 import candidate
from app.intraday_engine.v9 import IntradayEngineV9
from app.intraday_engine.v10 import IntradayEngineV10


def _abrupt_breakdown(**changes: object) -> AnalysisResult:
    result = candidate(
        confirmation_quality="strong",
        momentum_5_percent=Decimal("-0.4739"),
        relative_volume=Decimal("3.1357"),
        short_entry_efficiency_gate_passed=False,
        short_confirmation_gate_passed=False,
        short_confirmation_persistence=False,
        five_minute_lower_high=False,
        short_mature_confirmation_gate_passed=False,
        short_ema20_extension_warning=True,
        risk_ok=True,
        short_entry_lane="STANDARD",
    )
    metrics = {item.name: item.value for item in result.metrics}
    metrics.update(changes)
    return result.model_copy(
        update={
            "verdict": AnalysisVerdict.WATCH,
            "reasons": (
                "setup:bearish_breakdown",
                "short_late_entry_wait_retest",
                "short_lower_high_pending",
                "short_breakdown_persistence_pending",
                "short_ema20_retest_required",
            ),
            "metrics": tuple(
                NamedValue(name=name, value=value) for name, value in metrics.items()
            ),
        }
    )


def _run(monkeypatch: pytest.MonkeyPatch, base: AnalysisResult) -> AnalysisResult:
    monkeypatch.setattr(IntradayEngineV9, "analyze", lambda *args, **kwargs: base)
    return IntradayEngineV10().analyze(_efficient_bearish_context())


def test_abrupt_strong_breakdown_does_not_wait_for_a_retest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(monkeypatch, _abrupt_breakdown())
    metrics = {item.name: item.value for item in result.metrics}

    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert metrics["short_entry_lane"] == "IMPULSE_BREAKDOWN"
    assert metrics["short_impulse_breakdown_gate_passed"] is True
    assert metrics["short_mature_confirmation_gate_passed"] is True
    assert "short_impulse_breakdown_confirmed" in result.reasons
    assert "short_retest_waived_for_impulse" in result.reasons
    assert "short_late_entry_wait_retest" not in result.reasons


@pytest.mark.parametrize(
    "change",
    [
        {"confirmation_quality": "standard"},
        {"momentum_5_percent": Decimal("-0.39")},
        {"relative_volume": Decimal("2.49")},
        {"risk_ok": False},
        {"short_entry_efficiency_gate_passed": True},
        {"intraday_regime": "range_or_transition"},
        {"setup": "bearish_vwap_rejection"},
    ],
)
def test_impulse_lane_keeps_strict_evidence_guards(
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
) -> None:
    result = _run(monkeypatch, _abrupt_breakdown(**change))
    metrics = {item.name: item.value for item in result.metrics}

    assert result.verdict is AnalysisVerdict.WATCH
    assert metrics["short_impulse_breakdown_gate_passed"] is False
    assert metrics["short_mature_confirmation_gate_passed"] is False
