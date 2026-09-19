from pathlib import Path

from app.alert_engine.v311 import AlertEngineV311
from app.entry_opportunity_engine.v23 import EntryOpportunityEngineV23
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.intraday_engine.v10 import IntradayEngineV10

ROOT = Path(__file__).resolve().parents[3]


def test_v776_selects_short_safety_engines_and_preserves_rollback() -> None:
    previous = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.75.0.yaml")
    current = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.76.0.yaml")

    assert type(current.build_intraday()) is IntradayEngineV10
    assert type(current.build_alert()) is AlertEngineV311
    assert (
        type(current.build_entry_opportunity(store=object()))
        is EntryOpportunityEngineV23
    )
    assert current.spec(EngineSlot.INTRADAY).strategy.version == "1.6.0"
    assert current.spec(EngineSlot.ALERT).strategy.version == "1.5.0"
    for slot in current.definition.engines:
        if slot not in {
            EngineSlot.INTRADAY,
            EngineSlot.ALERT,
            EngineSlot.ENTRY_OPPORTUNITY,
        }:
            assert current.spec(slot) == previous.spec(slot)
