"""The CLI and Linux launcher select the complete latest engine assembly."""

import re
from pathlib import Path

import pytest

from app.common.settings import AppSettings
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.integration.engine_catalog import default_engine_registry

ROOT = Path(__file__).resolve().parents[3]


def test_default_entry_points_select_corrected_swing_trade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKETBOT_DEFINITION_PATH", raising=False)
    settings = AppSettings(_env_file=None)
    launcher = (ROOT / "scripts/linux/start-market-bot.sh").read_text(encoding="utf-8")
    selected = re.search(r'^DEFINITION_PATH="\$PROJECT_ROOT/([^"]+)"$', launcher, re.MULTILINE)
    assert selected is not None
    assert Path(selected[1]) == settings.definition_path
    assembly = MarketBotAssembly.from_path(ROOT / settings.definition_path)
    assert assembly.definition.version == "7.75.0"
    assert assembly.spec(EngineSlot.SWING_TRADE).implementation == "1.12.0"
    assert assembly.spec(EngineSlot.SWING_TRADE).strategy.version == "1.9.0"
    assert assembly.spec(EngineSlot.INTRADAY).implementation == "9.0.0"
    assert assembly.spec(EngineSlot.INTRADAY).strategy.version == "1.5.0"
    registry = default_engine_registry()
    for slot in registry.slots():
        latest = max(
            registry.registration(slot).implementations,
            key=lambda version: tuple(int(part) for part in version.split(".")),
        )
        assert assembly.spec(slot).implementation == latest, slot.value


def test_definition_override_preserves_explicit_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETBOT_DEFINITION_PATH", "configs/marketbot/7.69.0.yaml")
    settings = AppSettings(_env_file=None)
    assembly = MarketBotAssembly.from_path(ROOT / settings.definition_path)
    assert assembly.definition.version == "7.69.0"
    assert assembly.spec(EngineSlot.SWING_TRADE).strategy.version == "1.5.0"
