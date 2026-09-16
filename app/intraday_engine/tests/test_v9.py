# pyright: reportPrivateUsage=false
from decimal import Decimal
from pathlib import Path

import pytest

from app.contracts import AnalysisResult, AnalysisVerdict, NamedValue
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.intraday_engine.tests.test_v8 import candidate
from app.intraday_engine.v8 import IntradayEngineV8
from app.intraday_engine.v9 import IntradayEngineV9

from .test_v5 import _efficient_bearish_context


def _confirmed_standard(**changes: object) -> AnalysisResult:
    result = candidate(
        confirmation_quality="strong",
        short_mature_retest_confirmed=True,
        short_mature_confirmation_gate_passed=True,
        short_confirmation_persistence=True,
        short_ema20_extension_warning=False,
        short_entry_timing="efficient_lower_high",
        short_entry_lane="STANDARD",
        confirmation_gate_passed=True,
        mature_confirmation_gate_passed=True,
    )
    metrics = {item.name: item.value for item in result.metrics}
    metrics.update(changes)
    return result.model_copy(
        update={
            "verdict": AnalysisVerdict.FAVORABLE,
            "score": Decimal("100"),
            "confidence": Decimal("1"),
            "reasons": ("setup:bearish_breakdown",),
            "metrics": tuple(NamedValue(name=name, value=value) for name, value in metrics.items()),
        }
    )


def _run(monkeypatch: pytest.MonkeyPatch, base: AnalysisResult) -> AnalysisResult:
    monkeypatch.setattr(IntradayEngineV8, "analyze", lambda *args, **kwargs: base)
    return IntradayEngineV9().analyze(_efficient_bearish_context())


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"short_confirmation_persistence": False},
            "short_breakdown_persistence_pending",
        ),
        (
            {"short_ema20_extension_warning": True},
            "short_ema20_retest_required",
        ),
    ],
)
def test_standard_short_requires_persistence_and_valid_ema20_distance(
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
    reason: str,
) -> None:
    result = _run(monkeypatch, _confirmed_standard(**changes))
    metrics = {item.name: item.value for item in result.metrics}

    assert result.verdict is AnalysisVerdict.WATCH
    assert result.score == Decimal("64")
    assert metrics["short_standard_confirmation_gate_passed"] is False
    assert metrics["short_mature_confirmation_gate_passed"] is False
    assert metrics["short_mature_retest_confirmed"] is False
    assert metrics["short_entry_timing"] == "wait_short_retest"
    assert reason in result.reasons


def test_standard_short_remains_confirmed_with_both_independent_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(monkeypatch, _confirmed_standard())
    metrics = {item.name: item.value for item in result.metrics}

    assert result.verdict is AnalysisVerdict.FAVORABLE
    assert metrics["short_standard_confirmation_gate_passed"] is True


def test_asts_20260916_first_breakdown_close_stays_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(
        monkeypatch,
        _confirmed_standard(
            reference_price=Decimal("57.51"),
            short_entry_trigger_level=Decimal("57.56"),
            short_breakdown_extension_atr=Decimal("0.3411"),
            short_ema20_extension_atr=Decimal("2.1467"),
            short_confirmation_persistence=False,
            short_ema20_extension_warning=True,
        ),
    )

    assert result.verdict is AnalysisVerdict.WATCH
    assert "short_breakdown_persistence_pending" in result.reasons
    assert "short_ema20_retest_required" in result.reasons


def test_specialized_displacement_lane_preserves_its_stronger_flow_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(
        monkeypatch,
        _confirmed_standard(
            short_entry_lane="DISPLACEMENT",
            short_confirmation_persistence=False,
            short_ema20_extension_warning=True,
        ),
    )

    assert result.verdict is AnalysisVerdict.FAVORABLE


def test_assembly_selects_v9_and_preserves_v8_for_rollback() -> None:
    root = Path(__file__).resolve().parents[3]
    previous = MarketBotAssembly.from_path(root / "configs/marketbot/7.74.0.yaml")
    current = MarketBotAssembly.from_path(root / "configs/marketbot/7.75.0.yaml")

    assert type(previous.build_intraday()) is IntradayEngineV8
    assert type(current.build_intraday()) is IntradayEngineV9
    assert current.spec(EngineSlot.INTRADAY).strategy.version == "1.5.0"
    assert current.build_intraday()._short_ema20_extension_hard_gate is True
    for slot in previous.definition.engines:
        if slot is not EngineSlot.INTRADAY:
            assert current.spec(slot) == previous.spec(slot)
