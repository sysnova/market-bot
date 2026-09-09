from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    NamedValue,
    PatternDirection,
)
from app.integration.engine_assembly import MarketBotAssembly
from app.intraday_engine.v7 import IntradayEngineV7
from app.intraday_engine.v8 import IntradayEngineV8

from .short_replay import contexts
from .test_v5 import _efficient_bearish_context


def candidate(**changes: object) -> AnalysisResult:
    result = IntradayEngineV7().analyze(_efficient_bearish_context())
    metrics = {m.name: m.value for m in result.metrics}
    # Observed ASTS blockers at 2026-09-09 14:14 UTC, without symbol-specific policy.
    metrics.update(
        confirmation_quality="standard",
        momentum_5_percent=Decimal("-0.5187"),
        relative_volume=Decimal("1.4407"),
        short_mature_retest_confirmed=False,
        short_mature_confirmation_gate_passed=False,
        short_confirmation_persistence=False,
        confirmation_gate_passed=False,
        mature_confirmation_gate_passed=False,
        short_entry_timing="wait_short_retest",
        entry_timing="wait_short_retest",
    )
    metrics.update(changes)
    return result.model_copy(
        update={
            "verdict": AnalysisVerdict.WATCH,
            "score": Decimal("64"),
            "reasons": ("short_mature_retest_pending",),
            "metrics": tuple(NamedValue(name=k, value=v) for k, v in metrics.items()),
        }
    )


def run(monkeypatch: pytest.MonkeyPatch, base: AnalysisResult) -> AnalysisResult:
    monkeypatch.setattr(IntradayEngineV7, "analyze", lambda *a, **kw: base)
    return IntradayEngineV8().analyze(_efficient_bearish_context())


def test_standard_quality_can_confirm_efficient_momentum_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(monkeypatch, candidate())
    m = {v.name: v.value for v in result.metrics}
    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert m["short_entry_lane"] == "EARLY_BREAKDOWN"
    assert m["short_mature_confirmation_gate_passed"] is True
    assert m["confirmation_quality"] == "standard"
    assert m["short_mature_retest_confirmed"] is False
    assert "short_mature_retest_pending" not in result.reasons


@pytest.mark.parametrize(
    "change",
    [
        {"relative_volume": Decimal("1.29")},
        {"momentum_5_percent": Decimal("-0.49")},
        {"five_minute_lower_high": False},
        {"short_entry_efficiency_gate_passed": False},
        {"risk_ok": False},
        {"short_confirmation_gate_passed": False},
        {"confirmation_quality": "weak"},
        {"intraday_regime": "range_or_transition"},
        {"setup": "bullish_breakout"},
        {"setup": "bearish_vwap_rejection"},
    ],
)
def test_early_lane_keeps_evidence_guards(
    monkeypatch: pytest.MonkeyPatch, change: dict[str, object]
) -> None:
    result = run(monkeypatch, candidate(**change))
    assert result.verdict is AnalysisVerdict.WATCH
    assert (
        next(m.value for m in result.metrics if m.name == "short_mature_confirmation_gate_passed")
        is False
    )


def test_quality_blocker_is_not_reported_as_missing_lower_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = run(monkeypatch, candidate(relative_volume=Decimal("1.1")))
    assert "short_quality_pending" in result.reasons
    assert "short_mature_retest_pending" not in result.reasons


def test_assembly_selects_v8_and_preserves_v7() -> None:
    root = Path(__file__).resolve().parents[3]
    assert isinstance(
        MarketBotAssembly.from_path(root / "configs/marketbot/7.54.0.yaml").build_intraday(),
        IntradayEngineV8,
    )
    assert (
        type(MarketBotAssembly.from_path(root / "configs/marketbot/7.52.0.yaml").build_intraday())
        is IntradayEngineV7
    )
    active = MarketBotAssembly.from_path(root / "configs/marketbot/7.50.0.yaml")
    proposed = MarketBotAssembly.from_path(root / "configs/marketbot/7.54.0.yaml")
    for slot in active.definition.engines:
        if slot.value != "intraday":
            assert active.spec(slot) == proposed.spec(slot)


@pytest.mark.parametrize("swing_enabled", [True, False])
def test_real_sip_replay_reaches_alert_only_with_swing(swing_enabled: bool) -> None:
    root = Path(__file__).resolve().parents[3]
    assembly = MarketBotAssembly.from_path(root / "configs/marketbot/7.54.0.yaml")
    previous = MarketBotAssembly.from_path(root / "configs/marketbot/7.52.0.yaml")
    changed = []
    for context in contexts():
        old = previous.build_intraday().analyze(context)
        new = assembly.build_intraday().analyze(context)
        if old.verdict != new.verdict:
            changed.append((context, old, new))
    assert len(changed) == 1
    context, old, new = changed[0]
    assert context.as_of.isoformat() == "2026-09-09T14:14:00+00:00"
    assert old.verdict is AnalysisVerdict.WATCH
    assert new.verdict is AnalysisVerdict.FAVORABLE
    m = {v.name: v.value for v in new.metrics}
    assert m["reference_price"] == Decimal("64.255")
    assert m["confirmation_quality"] == "standard"
    swing = AnalysisResult(
        engine_id="swing",
        engine_version="15.0.0",
        symbol="ASTS",
        horizon=AnalysisHorizon.SWING,
        as_of=context.as_of - timedelta(minutes=29),
        verdict=AnalysisVerdict.AVOID,
        direction=PatternDirection.BEARISH,
        score=Decimal("0"),
        confidence=Decimal("0"),
        reasons=("short_thesis_broken",),
        metrics=(
            NamedValue(name="reference_price", value=Decimal("64.643")),
            NamedValue(name="reward_risk_to_resistance", value=Decimal("0.9886")),
            NamedValue(name="short_structure_gate_passed", value=swing_enabled),
            NamedValue(name="short_setup_id", value="swing-short:ASTS:2026-08-12"),
        ),
        context_hash="sha256:" + "8" * 64,
    )
    alert_engine = assembly.build_alert()
    now = context.as_of + timedelta(minutes=1)
    alert_engine.ingest(swing, now=now)
    alert = alert_engine.ingest(new, now=now)
    if swing_enabled:
        assert alert is not None
        assert alert.title == "ASTS SHORT CONFIRMED"
        levels = {v.name: v.value for v in alert.metrics}
        assert levels["short_invalidation"] == Decimal("64.4156")
        assert levels["short_target"] == Decimal("64.0140")
    else:
        assert alert is None or "short_entry_confirmed" not in alert.reasons


def test_disabled_early_lane_preserves_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    base = candidate()
    monkeypatch.setattr(IntradayEngineV7, "analyze", lambda *a, **kw: base)
    result = IntradayEngineV8(short_early_breakdown_enabled=False).analyze(
        _efficient_bearish_context()
    )
    assert result.verdict is AnalysisVerdict.WATCH
