from decimal import Decimal

import pytest

from app.contracts import AnalysisVerdict, PatternDirection
from app.swing_engine.tests.test_v6 import _bar, _context, _old_failed_breakout
from app.swing_engine.v14 import SwingEngineV14
from app.swing_engine.v15 import SwingEngineV15


def test_structurally_invalidated_long_thesis_remains_short_evidence() -> None:
    bars = _old_failed_breakout(count=60)
    bars[45] = _bar(45, close="98.90", high="99.20", low="98.50")
    context = _context(bars).model_copy(update={"price": Decimal("90")})

    result = SwingEngineV15().analyze(context)
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["failed_breakout_state"] == "STRUCTURE_INVALIDATED"
    assert metrics["failed_breakout"] is False
    assert metrics["short_structure_gate_passed"] is True
    assert metrics["short_thesis_broken"] is True
    assert str(metrics["short_setup_id"]).startswith("swing-short:TEST:")
    assert result.direction is PatternDirection.BEARISH
    assert result.verdict is AnalysisVerdict.AVOID
    assert result.engine_version == "15.0.0"
    previous = SwingEngineV14().analyze(context)
    assert (
        next(m.value for m in previous.metrics if m.name == "short_structure_gate_passed") is False
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_daily_price_drop_without_failed_long_structure_is_not_a_short(enabled: bool) -> None:
    bars = [_bar(index) for index in range(60)]
    context = _context(bars).model_copy(update={"price": Decimal("97")})

    result = SwingEngineV15(short_confirmation_enabled=enabled).analyze(context)
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["failed_breakout_state"] == "NONE"
    assert metrics["short_structure_gate_passed"] is False
    assert metrics["short_setup_id"] is None


@pytest.mark.parametrize("state", ["STRUCTURE_INVALIDATED", "VOLATILITY_INVALIDATED"])
def test_invalidated_long_evidence_keeps_price_and_policy_gates(state: str) -> None:
    metrics: dict[str, object] = {
        "failed_breakout_state": state,
        "failed_breakout_level": Decimal("100"),
        "daily_sma20": Decimal("110"),
        "daily_sma50": Decimal("110"),
        "price_vs_breakout_avwap_percent": Decimal("-5"),
    }
    assert SwingEngineV15()._short_thesis_broken(Decimal("90"), metrics) is True
    assert SwingEngineV15()._short_thesis_broken(Decimal("100"), metrics) is False
    assert SwingEngineV15()._short_thesis_broken(Decimal("101"), metrics) is False
    assert (
        SwingEngineV15(short_confirmation_enabled=False)._short_thesis_broken(
            Decimal("90"), metrics
        )
        is False
    )
    for name, value in (
        ("daily_sma20", Decimal("85")),
        ("daily_sma50", Decimal("90")),
        ("price_vs_breakout_avwap_percent", Decimal("1")),
        ("failed_breakout_level", None),
    ):
        assert (
            SwingEngineV15()._short_thesis_broken(Decimal("90"), {**metrics, name: value}) is False
        )


@pytest.mark.parametrize("state", ["RECOVERED", "SUPERSEDED", "EXPIRED", "NONE"])
def test_resolved_or_absent_breakouts_do_not_create_short_theses(state: str) -> None:
    metrics: dict[str, object] = {
        "failed_breakout_state": state,
        "failed_breakout_level": Decimal("100"),
        "daily_sma20": Decimal("110"),
        "daily_sma50": Decimal("110"),
        "price_vs_breakout_avwap_percent": Decimal("-5"),
    }
    assert SwingEngineV15()._short_thesis_broken(Decimal("90"), metrics) is False
