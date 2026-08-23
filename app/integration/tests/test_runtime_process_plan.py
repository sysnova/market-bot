from pathlib import Path

import pytest

from app.common.settings import AppSettings
from app.integration.engine_assembly import MarketBotAssembly
from app.integration.marketbot_definition import load_marketbot_definition
from app.integration.runtime_process_plan import (
    RuntimeProcessSpec,
    build_runtime_process_plan,
    startup_batches,
)


def test_runtime_plan_is_filtered_by_definition_mode_and_owns_commands() -> None:
    assembly = MarketBotAssembly.from_settings(AppSettings())

    plan = build_runtime_process_plan(
        assembly.definition,
        runtime_root=Path("C:/runtime root"),
        symbols="HIMS,ZETA",
        bell=False,
    )

    names = {process.name for process in plan.processes}
    assert "entry-recovery" in names
    assert "signal-fusion-v0" not in names
    assert "volume-structure-v1" in names
    assert "options-gamma-v1" in names
    assert "news-intelligence-v1" in names
    assert "swing-channel-4h" in names
    assert "4hgeri" in names
    assert "patreon-caps-v1" not in names
    assert "elliott-wave-v0" not in names
    assert "support-confirmation-v0" in names
    assert "dilution-sec" not in names
    assert "peter-lynch" not in names

    long_term = plan.process("long-term")
    assert long_term.arguments[:4] == ("run", "marketbot", "engine", "long")
    assert long_term.arguments[-2:] == ("--symbols", "HIMS,ZETA")
    assert long_term.ready_path == Path("C:/runtime root/status/long-term.ready.json")

    swing_channel = plan.process("swing-channel-4h")
    assert swing_channel.arguments[-2:] == ("--symbols", "HIMS,ZETA")
    assert swing_channel.dependencies == ("market-history-v1", "long-portfolio-v1")

    geri = plan.process("4hgeri")
    assert geri.arguments[-2:] == ("--symbols", "HIMS,ZETA")
    assert geri.dependencies == ("market-history-v1", "support-confirmation-v0")

    confirmed = plan.process("confirmed-buy-monitor")
    assert confirmed.operator_monitor is True
    assert confirmed.dependencies == ("alert",)
    assert "--no-bell" in confirmed.arguments


def test_runtime_plan_centralizes_dependency_batches() -> None:
    assembly = MarketBotAssembly.from_settings(AppSettings())
    plan = build_runtime_process_plan(assembly.definition, runtime_root=Path(".runtime"))

    batches = startup_batches(plan.headless_processes)
    positions = {name: index for index, batch in enumerate(batches) for name in batch}

    assert positions["outbox-relay"] < positions["alert"]
    assert positions["outbox-relay"] < positions["market-history-v1"]
    assert positions["market-history-v1"] < positions["entry-opportunity"]
    assert "market-history-v1" in plan.process("entry-opportunity").dependencies
    assert positions["market-history-v1"] < positions["long-portfolio-v1"]
    assert positions["long-portfolio-v1"] < positions["long-term"]
    assert positions["long-term"] < positions["alpaca-market-stream"]
    assert "signal-fusion-v0" not in positions
    assert positions["volume-structure-v1"] < positions["alpaca-market-stream"]
    assert "options-gamma-v1" not in plan.process("alpaca-market-stream").dependencies
    assert "confirmed-buy-monitor" not in plan.process("alpaca-market-stream").dependencies
    assert plan.process("news-intelligence-v1").dependencies == (
        "alert",
        "entry-watcher",
        "entry-opportunity",
    )
    assert positions["entry-opportunity"] < positions["news-intelligence-v1"]
    assert positions["news-intelligence-v1"] < positions["alpaca-market-stream"]


def test_v727_starts_support_before_the_enriched_swing_engines() -> None:
    definition = load_marketbot_definition(
        Path(__file__).parents[3] / "configs/marketbot/7.27.0.yaml"
    )
    plan = build_runtime_process_plan(definition, runtime_root=Path(".runtime"))
    batches = startup_batches(plan.headless_processes)
    positions = {name: index for index, batch in enumerate(batches) for name in batch}

    assert "support-confirmation-v0" in positions
    for consumer in ("swing", "4hgeri", "swing-trade"):
        assert "support-confirmation-v0" in plan.process(consumer).dependencies
        assert positions["support-confirmation-v0"] < positions[consumer]


def test_startup_batches_reject_missing_dependencies_and_cycles() -> None:
    missing = RuntimeProcessSpec(name="a", arguments=("run",), dependencies=("missing",))
    with pytest.raises(ValueError, match="missing process dependency"):
        startup_batches((missing,))

    cyclic = (
        RuntimeProcessSpec(name="a", arguments=("run",), dependencies=("b",)),
        RuntimeProcessSpec(name="b", arguments=("run",), dependencies=("a",)),
    )
    with pytest.raises(ValueError, match="cycle"):
        startup_batches(cyclic)
