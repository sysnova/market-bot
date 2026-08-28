from decimal import Decimal

from app.contracts import AnalysisVerdict, PatternDirection
from app.swing_engine.tests.test_v6 import _context, _old_failed_breakout
from app.swing_engine.v14 import SwingEngineV14


def test_v14_marks_an_active_failed_breakout_below_daily_thesis_as_short() -> None:
    context = _context(_old_failed_breakout(count=60)).model_copy(
        update={"price": Decimal("90")}
    )

    result = SwingEngineV14().analyze(context)
    metrics = {item.name: item.value for item in result.metrics}

    assert result.engine_version == "14.0.0"
    assert result.direction is PatternDirection.BEARISH
    assert result.verdict is AnalysisVerdict.AVOID
    assert metrics["classification"] == "avoid"
    assert metrics["short_thesis_broken"] is True
    assert metrics["short_structure_gate_passed"] is True
    assert str(metrics["short_setup_id"]).startswith("swing-short:TEST:")
    assert "failed_breakout_short_thesis_broken" in result.reasons


def test_v14_does_not_break_the_thesis_while_price_holds_daily_structure() -> None:
    result = SwingEngineV14().analyze(_context(_old_failed_breakout(count=60)))
    metrics = {item.name: item.value for item in result.metrics}

    assert metrics["failed_breakout_state"] == "ACTIVE"
    assert metrics["short_thesis_broken"] is False
    assert metrics["short_structure_gate_passed"] is False
