import ast
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.alert_engine import AlertEngineV3
from app.common.settings import AppSettings
from app.entry_opportunity_engine import (
    EntryOpportunityEngine,
    InMemoryEntryOpportunityStore,
)
from app.entry_watcher import (
    EntryWatcherV5,
    InMemoryEntryWatchStore,
)
from app.integration.engine_assembly import (
    EngineSlot,
    MarketBotAssembly,
    load_marketbot_definition,
)
from app.intraday_engine import IntradayEngineV3, IntradayEngineV4
from app.long_term_engine import LongTermEngineV2
from app.portfolio_flow_engine import PortfolioFlowEngineV1, PortfolioFlowEngineV2
from app.swing_engine import SwingEngineV3

ROOT = Path(__file__).resolve().parents[3]
DEFINITION = ROOT / "configs/marketbot/6.0.0.yaml"
PREVIOUS_DEFINITION = ROOT / "configs/marketbot/5.0.0.yaml"
INTEGRATION = ROOT / "app/integration"


def test_default_definition_declares_every_engine_slot_and_strategy() -> None:
    definition = load_marketbot_definition(DEFINITION)

    assert set(definition.engines) == set(EngineSlot)
    assert definition.version == "6.0.0"
    assert all(item.strategy.version for item in definition.engines.values())
    assert definition.engines[EngineSlot.INTRADAY].implementation == "4.0.0"
    assert definition.engines[EngineSlot.PORTFOLIO_FLOW].implementation == "2.0.0"
    assert (
        definition.engines[EngineSlot.LONG_PORTFOLIO].strategy.artifact
        == ROOT / "configs/rules/long_portfolio/1.0.0.yaml"
    )


def test_one_assembly_builds_the_core_and_alert_implementations() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)

    assert isinstance(assembly.build_long_term(), LongTermEngineV2)
    assert isinstance(assembly.build_swing(), SwingEngineV3)
    assert isinstance(assembly.build_intraday(), IntradayEngineV4)
    assert isinstance(assembly.build_alert(), AlertEngineV3)
    assert isinstance(
        assembly.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV5,
    )
    opportunity = assembly.build_entry_opportunity(store=InMemoryEntryOpportunityStore())
    assert isinstance(opportunity, EntryOpportunityEngine)
    assert opportunity.engine_id == "entry-opportunity"
    assert assembly.spec(EngineSlot.ENTRY_OPPORTUNITY).implementation == "1.0.0"
    assert isinstance(assembly.build_portfolio_flow(), PortfolioFlowEngineV2)


def test_confirmation_strategy_artifact_is_injected_into_selected_implementations() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)

    swing = assembly.build_swing()
    intraday = assembly.build_intraday()
    watcher = assembly.build_entry_watcher(store=InMemoryEntryWatchStore())

    assert swing._strategy_version == "5.0.0"
    assert intraday._minimum_momentum_percent == Decimal("0.15")
    assert intraday._maximum_trigger_extension_atr == Decimal("0.50")
    assert intraday._maximum_ema20_extension_atr == Decimal("2.00")
    assert watcher._minimum_reconfirmation_delay == timedelta(minutes=3)
    assert watcher._policy.trigger_rearm_cooldown == timedelta(minutes=30)
    assert watcher._no_retest_higher_low_enabled is True
    alert = assembly.build_alert()
    assert alert._minimum_reconfirmation_delay == timedelta(minutes=3)
    assert alert._strong_confirmation_required is True
    assert alert._five_minute_higher_low_required is True
    assert alert._same_market_session_required is True


def test_settings_load_the_definition_as_the_primary_source() -> None:
    settings = AppSettings(definition_path=DEFINITION, _env_file=None)

    assembly = MarketBotAssembly.from_settings(settings)

    assert assembly.definition.version == "6.0.0"
    assert isinstance(assembly.build_intraday(), IntradayEngineV4)


def test_previous_definition_preserves_alert_v2_for_replay() -> None:
    assembly = MarketBotAssembly.from_path(PREVIOUS_DEFINITION)

    assert assembly.build_alert().engine_version == "2.0.0"


def test_legacy_confirmation_setting_rolls_back_one_compatible_bundle() -> None:
    settings = AppSettings(
        definition_path=DEFINITION,
        entry_confirmation_rule_version="3.0.0",
        _env_file=None,
    )

    assembly = MarketBotAssembly.from_settings(settings)

    assert assembly.spec(EngineSlot.SWING).implementation == "3.0.0"
    assert assembly.spec(EngineSlot.INTRADAY).implementation == "3.0.0"
    assert assembly.spec(EngineSlot.ENTRY_WATCHER).implementation == "3.0.0"
    assert assembly.spec(EngineSlot.INTRADAY).strategy.version == "3.0.0"


def test_portfolio_flow_v1_remains_a_real_rollback_implementation() -> None:
    definition = load_marketbot_definition(DEFINITION)
    engines = dict(definition.engines)
    engines[EngineSlot.PORTFOLIO_FLOW] = replace(
        engines[EngineSlot.PORTFOLIO_FLOW],
        implementation="1.0.0",
        strategy=replace(
            engines[EngineSlot.PORTFOLIO_FLOW].strategy,
            version="1.0.0",
            artifact=ROOT / "configs/rules/portfolio_flow/1.0.0.yaml",
        ),
    )
    assembly = MarketBotAssembly(replace(definition, engines=engines))

    assert isinstance(assembly.build_portfolio_flow(), PortfolioFlowEngineV1)


def test_definition_does_not_pin_consumers_to_producer_implementation_versions() -> None:
    definition = load_marketbot_definition(DEFINITION)
    engines = dict(definition.engines)
    engines[EngineSlot.INTRADAY] = replace(
        engines[EngineSlot.INTRADAY],
        implementation="3.0.0",
        strategy=replace(
            engines[EngineSlot.INTRADAY].strategy,
            version="3.0.0",
            artifact=ROOT / "configs/rules/entry_confirmation/3.0.0.yaml",
        ),
    )

    assembly = MarketBotAssembly(replace(definition, engines=engines))

    assert isinstance(assembly.build_intraday(), IntradayEngineV3)
    assert isinstance(
        assembly.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV5,
    )


def test_unknown_implementation_fails_before_any_process_starts() -> None:
    definition = load_marketbot_definition(DEFINITION)
    engines = dict(definition.engines)
    engines[EngineSlot.ALERT] = replace(engines[EngineSlot.ALERT], implementation="99.0.0")

    with pytest.raises(ValueError, match="unregistered implementation"):
        MarketBotAssembly(replace(definition, engines=engines))


def test_operational_compositions_cannot_construct_catalog_engines_directly() -> None:
    catalog_names = {
        "AlertEngine",
        "AlertEngineV2",
        "AlertEngineV3",
        "DilutionSecEngine",
        "ElliottWaveEngine",
        "EntryWatcher",
        "EntryWatcherV2",
        "EntryWatcherV3",
        "EntryWatcherV4",
        "EntryWatcherV5",
        "EntryOpportunityEngine",
        "EntryOpportunityManager",
        "IntradayEngine",
        "IntradayEngineV2",
        "IntradayEngineV3",
        "IntradayEngineV4",
        "LongPortfolioEngine",
        "LongTermEngine",
        "LongTermEngineV2",
        "PatreonCapsEngine",
        "PeterLynchEngine",
        "PortfolioFlowEngineV1",
        "PortfolioFlowEngineV2",
        "RotationEngine",
        "SignalFusionEngine",
        "SupportConfirmationEngine",
        "SwingEngine",
        "SwingEngineV2",
        "SwingEngineV3",
    }
    violations: list[str] = []
    for path in INTEGRATION.glob("*.py"):
        if path.name == "engine_assembly.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in catalog_names
            ):
                violations.append(f"{path.name}:{node.lineno}:{node.func.id}")

    assert violations == []
