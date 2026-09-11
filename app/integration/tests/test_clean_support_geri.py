from pathlib import Path

from app.integration.engine_assembly import MarketBotAssembly
from app.integration.swing_4h_geri_monitor import _format_assessment
from app.swing_4h_geri_engine.tests.test_v18 import _context
from app.swing_4h_geri_engine.tests.test_v112 import bars
from app.swing_4h_geri_engine.v112 import Swing4HGeriEngineV112


def test_assembly_and_monitor_use_structural_zone() -> None:
    assembly = MarketBotAssembly.from_path(Path("configs/marketbot/7.56.0.yaml"))
    engine = assembly.build_4hgeri()
    assert isinstance(engine, Swing4HGeriEngineV112)
    result = engine.analyze(_context(bars(), "11"))
    rendered = _format_assessment(result, color=False)
    assert "zona 9-16" in rendered
    assert "confirma swing: cierre 4H > N2 16" in rendered
    assert "ATR-px" not in rendered


def test_recent_assembly_reports_optional_fibonacci_without_confirmation() -> None:
    from app.swing_4h_geri_engine.tests.test_v113 import history
    from app.swing_4h_geri_engine.v113 import Swing4HGeriEngineV113

    assembly = MarketBotAssembly.from_path(Path("configs/marketbot/7.57.0.yaml"))
    engine = assembly.build_4hgeri()
    assert isinstance(engine, Swing4HGeriEngineV113)
    result = engine.analyze(_context(history(), "14"))
    rendered = _format_assessment(result, color=False)
    assert "FIBO CONTEXTO | PLUS SI" in rendered
    assert "4H NO" in rendered
    assert "no confirma entrada" in rendered
