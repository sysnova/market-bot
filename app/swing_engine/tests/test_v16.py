from decimal import Decimal

from app.swing_engine.tests.test_v10 import _context
from app.swing_engine.v15 import SwingEngineV15
from app.swing_engine.v16 import SwingEngineV16


def test_recovery_preserves_original_trigger_and_rebound_geometry_without_changing_verdict() -> (
    None
):
    context = _context()
    before = SwingEngineV15(recovery_maximum_risk_atr=Decimal("10")).analyze(context)
    after = SwingEngineV16(recovery_maximum_risk_atr=Decimal("10")).analyze(context)
    m = {item.name: item.value for item in after.metrics}
    assert m["classification"] == "recovery"
    assert m["recovery_breakout_level"] == max(b.high for b in context.intraday_bars[-4:-1])
    assert m["recovery_intraday_rebound_low"] == min(b.low for b in context.intraday_bars[-2:])
    assert before.verdict == after.verdict and before.score == after.score
