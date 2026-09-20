from pathlib import Path

from app.alert_engine.v312 import AlertEngineV312
from app.integration.engine_assembly import EngineSlot, MarketBotAssembly
from app.integration.leveraged_thesis_composition import leveraged_thesis_source_subjects
from app.integration.runtime_process_plan import build_runtime_process_plan
from app.leveraged_thesis_engine.v13 import LeveragedThesisEngineV13

ROOT = Path(__file__).resolve().parents[3]


def test_v777_moves_short_ownership_to_leveraged_thesis() -> None:
    previous = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.76.0.yaml")
    current = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.77.0.yaml")

    assert type(current.build_alert()) is AlertEngineV312
    assert type(current.build_leveraged_thesis()) is LeveragedThesisEngineV13
    assert current.spec(EngineSlot.ALERT).strategy.version == "1.6.0"
    for slot in current.definition.engines:
        if slot not in {EngineSlot.ALERT, EngineSlot.LEVERAGED_THESIS}:
            assert current.spec(slot) == previous.spec(slot)


def test_v13_consumes_swing_and_intraday_without_reconsuming_action_alerts() -> None:
    engine = MarketBotAssembly.from_path(
        ROOT / "configs/marketbot/7.77.0.yaml"
    ).build_leveraged_thesis()

    subjects = leveraged_thesis_source_subjects(engine)

    assert "marketbot.v1.analysis.result.SWING.ASTS" in subjects
    assert "marketbot.v1.analysis.result.INTRADAY.ASTS" in subjects
    assert all(".alert.local." not in subject for subject in subjects)


def test_v13_process_waits_for_both_analysis_producers() -> None:
    assembly = MarketBotAssembly.from_path(ROOT / "configs/marketbot/7.77.0.yaml")
    process = build_runtime_process_plan(
        assembly.definition, runtime_root=Path(".runtime")
    ).process("leveraged-thesis")

    assert process.dependencies == (
        "ticker-cache",
        "swing",
        "intraday",
        "order-flow",
        "support-confirmation-v0",
    )
