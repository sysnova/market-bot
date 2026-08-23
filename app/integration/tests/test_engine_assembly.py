import ast
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.alert_engine import (
    AlertEngineV33,
    AlertEngineV35,
    AlertEngineV36,
    AlertEngineV37,
    AlertEngineV38,
)
from app.common.settings import AppSettings
from app.entry_opportunity_engine import (
    EntryOpportunityEngine,
    EntryOpportunityEngineV3,
    EntryOpportunityEngineV4,
    EntryOpportunityEngineV5,
    EntryOpportunityEngineV6,
    EntryOpportunityEngineV7,
    InMemoryEntryOpportunityStore,
)
from app.entry_recovery_engine import EntryRecoveryEngineV11
from app.entry_watcher import (
    EntryWatcherV5,
    EntryWatcherV52,
    EntryWatcherV53,
    EntryWatcherV54,
    EntryWatcherV55,
    InMemoryEntryWatchStore,
)
from app.integration.engine_assembly import (
    EngineMode,
    EngineSlot,
    MarketBotAssembly,
    load_marketbot_definition,
)
from app.integration.engine_registry import EngineRegistration, EngineRegistry
from app.integration.marketbot_definition import (
    EngineMode as DefinitionEngineMode,
)
from app.integration.marketbot_definition import (
    EngineSlot as DefinitionEngineSlot,
)
from app.integration.marketbot_definition import (
    load_marketbot_definition as load_definition_model,
)
from app.intraday_engine import IntradayEngineV3, IntradayEngineV4
from app.intraday_opportunity_engine import (
    InMemoryIntradayOpportunityStore,
    IntradayOpportunityEngine,
)
from app.long_portfolio_engine import LongPortfolioEngine, LongPortfolioPolicy, PortfolioAllocation
from app.long_term_engine import LongTermEngineV2
from app.news_intelligence_engine import NewsIntelligenceEngine
from app.options_gamma_engine import OptionsGammaEngine
from app.order_flow_engine import OrderFlowEngine
from app.patreon_caps_engine import PatreonCapsEngine, PatreonCapsPolicy
from app.portfolio_flow_engine import PortfolioFlowEngineV1, PortfolioFlowEngineV2
from app.scalp_engine import ScalpEngine
from app.signal_fusion_engine import SignalFusionEngineV05
from app.support_confirmation_engine import (
    SupportConfirmationEngine,
    SupportConfirmationEngineV03,
)
from app.swing_4h_geri_engine import (
    Swing4HGeriEngine,
    Swing4HGeriEngineV11,
    Swing4HGeriEngineV12,
    Swing4HGeriEngineV13,
    Swing4HGeriEngineV14,
    Swing4HGeriEngineV16,
    Swing4HGeriEngineV17,
)
from app.swing_channel_4h_engine import SwingChannel4HEngine, SwingChannel4HEngineV11
from app.swing_engine import (
    SwingEngineV4,
    SwingEngineV5,
    SwingEngineV6,
    SwingEngineV7,
    SwingEngineV8,
    SwingEngineV9,
    SwingEngineV10,
    SwingEngineV12,
    SwingEngineV13,
)
from app.swing_trade_engine import (
    SwingTradeEngine,
    SwingTradeEngineV11,
    SwingTradeEngineV13,
    SwingTradeEngineV14,
    SwingTradeEngineV15,
)
from app.volume_structure_engine import VolumeStructureEngineV11

ROOT = Path(__file__).resolve().parents[3]
DEFINITION = ROOT / "configs/marketbot/7.2.0.yaml"
VOLUME_STRUCTURE_DEFINITION = ROOT / "configs/marketbot/7.3.0.yaml"
INVALIDATION_DEFINITION = ROOT / "configs/marketbot/7.4.0.yaml"
LATEST_DEFINITION = ROOT / "configs/marketbot/7.5.0.yaml"
GAMMA_DEFINITION = ROOT / "configs/marketbot/7.6.0.yaml"
EARLY_RADAR_DEFINITION = ROOT / "configs/marketbot/7.7.0.yaml"
STRUCTURAL_SWING_DEFINITION = ROOT / "configs/marketbot/7.8.0.yaml"
PULLBACK_ENTRY_DEFINITION = ROOT / "configs/marketbot/7.9.0.yaml"
PREVIOUS_DEFINITION = ROOT / "configs/marketbot/7.1.0.yaml"
INTEGRATION = ROOT / "app/integration"
NEWS_DEFINITION = ROOT / "configs/marketbot/7.12.0.yaml"
VISIBLE_NEWS_DEFINITION = ROOT / "configs/marketbot/7.13.0.yaml"
SWING_CHANNEL_DEFINITION = ROOT / "configs/marketbot/7.14.0.yaml"
GERI_DEFINITION = ROOT / "configs/marketbot/7.15.0.yaml"
PINNED_SWING_CHANNEL_DEFINITION = ROOT / "configs/marketbot/7.16.0.yaml"
PINNED_GERI_DEFINITION = ROOT / "configs/marketbot/7.17.0.yaml"
FAILED_BREAKOUT_FSM_DEFINITION = ROOT / "configs/marketbot/7.18.0.yaml"
MIRRORED_GERI_DEFINITION = ROOT / "configs/marketbot/7.19.0.yaml"
SWING_TRADE_DEFINITION = ROOT / "configs/marketbot/7.20.0.yaml"
COUNTERTREND_GERI_DEFINITION = ROOT / "configs/marketbot/7.21.0.yaml"
COUNTERTREND_OPPORTUNITY_DEFINITION = ROOT / "configs/marketbot/7.22.0.yaml"
CONFIRMED_ENTRY_DEFINITION = ROOT / "configs/marketbot/7.23.0.yaml"
STRUCTURE_RECOVERY_DEFINITION = ROOT / "configs/marketbot/7.24.0.yaml"
CORRECTION_AVWAP_DEFINITION = ROOT / "configs/marketbot/7.25.0.yaml"
REARMED_RECOVERY_DEFINITION = ROOT / "configs/marketbot/7.26.0.yaml"
PARALLEL_SWING_LEGS_DEFINITION = ROOT / "configs/marketbot/7.28.0.yaml"
ACTIONABLE_SUPPORT_DEFINITION = ROOT / "configs/marketbot/7.29.0.yaml"
SUPPORT_ENRICHED_SWING_DEFINITION = ROOT / "configs/marketbot/7.30.0.yaml"
STABLE_SWING_THESIS_DEFINITION = ROOT / "configs/marketbot/7.31.0.yaml"
MICROSTRUCTURE_DEFINITION = ROOT / "configs/marketbot/7.32.0.yaml"


def test_microstructure_definition_adds_operational_paper_engines() -> None:
    assembly = MarketBotAssembly.from_path(MICROSTRUCTURE_DEFINITION)

    assert assembly.definition.version == "7.32.0"
    assert isinstance(assembly.build_order_flow(), OrderFlowEngine)
    assert isinstance(assembly.build_scalp(), ScalpEngine)
    assert isinstance(assembly.build_swing(), SwingEngineV13)
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngineV17)
    assert isinstance(assembly.build_swing_trade(), SwingTradeEngineV15)
    assert isinstance(
        assembly.build_intraday_opportunity(store=InMemoryIntradayOpportunityStore()),
        IntradayOpportunityEngine,
    )


def test_news_definition_activates_versioned_classifier_and_news_gate() -> None:
    assembly = MarketBotAssembly.from_path(NEWS_DEFINITION)

    assert assembly.definition.version == "7.12.0"
    assert isinstance(assembly.build(EngineSlot.NEWS_INTELLIGENCE), NewsIntelligenceEngine)
    assert isinstance(assembly.build_alert(), AlertEngineV36)


def test_visible_news_definition_keeps_buy_signals_and_marks_risk() -> None:
    assembly = MarketBotAssembly.from_path(VISIBLE_NEWS_DEFINITION)

    assert assembly.definition.version == "7.13.0"
    assert isinstance(assembly.build_alert(), AlertEngineV37)


def test_swing_channel_definition_adds_independent_shadow_engine() -> None:
    assembly = MarketBotAssembly.from_path(SWING_CHANNEL_DEFINITION)

    assert assembly.definition.version == "7.14.0"
    assert isinstance(assembly.build_swing_channel_4h(), SwingChannel4HEngine)
    assert assembly.spec(EngineSlot.SWING_CHANNEL_4H).mode is EngineMode.ACTIVE


def test_4hgeri_definition_keeps_both_swing_models_and_adds_third_shadow() -> None:
    assembly = MarketBotAssembly.from_path(GERI_DEFINITION)

    assert assembly.definition.version == "7.15.0"
    assert isinstance(assembly.build_swing_channel_4h(), SwingChannel4HEngine)
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngine)
    assert assembly.spec(EngineSlot.GERI_4H).mode is EngineMode.ACTIVE


def test_pinned_swing_channel_definition_preserves_active_geometry() -> None:
    assembly = MarketBotAssembly.from_path(PINNED_SWING_CHANNEL_DEFINITION)

    assert assembly.definition.version == "7.16.0"
    assert isinstance(assembly.build_swing_channel_4h(), SwingChannel4HEngineV11)
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngine)


def test_pinned_geri_definition_preserves_the_active_level_chain() -> None:
    assembly = MarketBotAssembly.from_path(PINNED_GERI_DEFINITION)

    assert assembly.definition.version == "7.17.0"
    assert isinstance(assembly.build_swing_channel_4h(), SwingChannel4HEngineV11)
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngineV11)


def test_failed_breakout_fsm_definition_activates_swing_v6_without_mutating_v5() -> None:
    previous = MarketBotAssembly.from_path(PINNED_GERI_DEFINITION)
    assembly = MarketBotAssembly.from_path(FAILED_BREAKOUT_FSM_DEFINITION)

    assert isinstance(previous.build_swing(), SwingEngineV5)
    assert assembly.definition.version == "7.18.0"
    assert isinstance(assembly.build_swing(), SwingEngineV6)
    assert isinstance(assembly.build_swing_channel_4h(), SwingChannel4HEngineV11)
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngineV11)
    assert assembly.spec(EngineSlot.SWING).strategy.version == "2.0.0"
    swing = assembly.build_swing()
    assert swing._failed_breakout_failure_window_days == 5
    assert swing._failed_breakout_maximum_age_days == 60
    assert swing._failed_breakout_structural_reset_lookback_days == 20
    assert swing._failed_breakout_reset_atr_multiple == Decimal("5")


def test_mirrored_geri_definition_keeps_v11_and_selects_standalone_v12() -> None:
    previous = MarketBotAssembly.from_path(FAILED_BREAKOUT_FSM_DEFINITION)
    assembly = MarketBotAssembly.from_path(MIRRORED_GERI_DEFINITION)

    assert previous.definition.version == "7.18.0"
    assert isinstance(previous.build_4hgeri(), Swing4HGeriEngineV11)
    assert assembly.definition.version == "7.19.0"
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngineV12)
    assert assembly.spec(EngineSlot.GERI_4H).strategy.version == "1.2.0"


def test_swing_trade_definition_adds_independent_versioned_engine() -> None:
    previous = MarketBotAssembly.from_path(MIRRORED_GERI_DEFINITION)
    assembly = MarketBotAssembly.from_path(SWING_TRADE_DEFINITION)

    assert previous.definition.version == "7.19.0"
    assert EngineSlot.SWING_TRADE not in previous.definition.engines
    assert assembly.definition.version == "7.20.0"
    assert isinstance(assembly.build_swing_trade(), SwingTradeEngine)
    assert isinstance(
        assembly.build_entry_opportunity(store=InMemoryEntryOpportunityStore()),
        EntryOpportunityEngineV4,
    )
    assert assembly.spec(EngineSlot.SWING_TRADE).strategy.version == "1.0.0"


def test_countertrend_geri_definition_preserves_v12_and_selects_v13() -> None:
    previous = MarketBotAssembly.from_path(SWING_TRADE_DEFINITION)
    assembly = MarketBotAssembly.from_path(COUNTERTREND_GERI_DEFINITION)

    assert isinstance(previous.build_4hgeri(), Swing4HGeriEngineV12)
    assert previous.definition.version == "7.20.0"
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngineV13)
    assert assembly.definition.version == "7.21.0"
    assert assembly.spec(EngineSlot.GERI_4H).strategy.version == "1.3.0"


def test_countertrend_opportunity_definition_preserves_v4_and_selects_v5() -> None:
    previous = MarketBotAssembly.from_path(COUNTERTREND_GERI_DEFINITION)
    assembly = MarketBotAssembly.from_path(COUNTERTREND_OPPORTUNITY_DEFINITION)

    assert isinstance(
        previous.build_entry_opportunity(store=InMemoryEntryOpportunityStore()),
        EntryOpportunityEngineV4,
    )
    assert previous.definition.version == "7.21.0"
    assert isinstance(
        assembly.build_entry_opportunity(store=InMemoryEntryOpportunityStore()),
        EntryOpportunityEngineV5,
    )
    assert assembly.definition.version == "7.22.0"
    assert assembly.spec(EngineSlot.ENTRY_OPPORTUNITY).strategy.version == "5.0.0"


def test_parallel_swing_legs_definition_selects_entry_opportunity_v6() -> None:
    assembly = MarketBotAssembly.from_path(PARALLEL_SWING_LEGS_DEFINITION)

    assert isinstance(
        assembly.build_entry_opportunity(store=InMemoryEntryOpportunityStore()),
        EntryOpportunityEngineV6,
    )
    assert assembly.definition.version == "7.28.0"
    assert assembly.spec(EngineSlot.ENTRY_OPPORTUNITY).strategy.version == "6.0.0"


def test_actionable_support_definition_selects_support_confirmation_v03() -> None:
    previous = MarketBotAssembly.from_path(PARALLEL_SWING_LEGS_DEFINITION)
    assembly = MarketBotAssembly.from_path(ACTIONABLE_SUPPORT_DEFINITION)

    assert isinstance(previous.build_support_confirmation(), SupportConfirmationEngine)
    assert isinstance(assembly.build_support_confirmation(), SupportConfirmationEngineV03)
    assert assembly.definition.version == "7.29.0"
    assert assembly.spec(EngineSlot.SUPPORT_CONFIRMATION).strategy.version == "0.3.0"


def test_support_enriched_swing_definition_versions_all_three_consumers() -> None:
    previous = MarketBotAssembly.from_path(ACTIONABLE_SUPPORT_DEFINITION)
    assembly = MarketBotAssembly.from_path(SUPPORT_ENRICHED_SWING_DEFINITION)

    assert not isinstance(previous.build_swing(), SwingEngineV12)
    assert isinstance(assembly.build_swing(), SwingEngineV12)
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngineV16)
    assert isinstance(assembly.build_swing_trade(), SwingTradeEngineV13)
    assert assembly.definition.version == "7.30.0"


def test_stable_swing_thesis_definition_updates_matching_structure() -> None:
    previous = MarketBotAssembly.from_path(SUPPORT_ENRICHED_SWING_DEFINITION)
    assembly = MarketBotAssembly.from_path(STABLE_SWING_THESIS_DEFINITION)

    assert isinstance(previous.build_swing_trade(), SwingTradeEngineV13)
    assert isinstance(
        previous.build_entry_opportunity(store=InMemoryEntryOpportunityStore()),
        EntryOpportunityEngineV6,
    )
    assert isinstance(assembly.build_swing_trade(), SwingTradeEngineV14)
    assert isinstance(
        assembly.build_entry_opportunity(store=InMemoryEntryOpportunityStore()),
        EntryOpportunityEngineV7,
    )
    assert assembly.definition.version == "7.31.0"


def test_confirmed_entry_definition_versions_causal_buy_analysis() -> None:
    previous = MarketBotAssembly.from_path(COUNTERTREND_OPPORTUNITY_DEFINITION)
    assembly = MarketBotAssembly.from_path(CONFIRMED_ENTRY_DEFINITION)

    assert isinstance(previous.build_swing(), SwingEngineV6)
    assert isinstance(previous.build_4hgeri(), Swing4HGeriEngineV13)
    assert isinstance(previous.build_swing_trade(), SwingTradeEngine)
    assert assembly.definition.version == "7.23.0"
    assert isinstance(assembly.build_swing(), SwingEngineV7)
    assert isinstance(assembly.build_4hgeri(), Swing4HGeriEngineV14)
    assert isinstance(assembly.build_swing_trade(), SwingTradeEngineV11)
    assert assembly.spec(EngineSlot.GERI_4H).strategy.version == "1.0.0"
    assert assembly.spec(EngineSlot.SWING_TRADE).strategy.version == "1.1.0"


def test_structure_recovery_definition_adds_independent_swing_entry_lane() -> None:
    previous = MarketBotAssembly.from_path(CONFIRMED_ENTRY_DEFINITION)
    assembly = MarketBotAssembly.from_path(STRUCTURE_RECOVERY_DEFINITION)

    assert isinstance(previous.build_swing(), SwingEngineV7)
    assert isinstance(
        previous.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV54,
    )
    assert assembly.definition.version == "7.24.0"
    assert isinstance(assembly.build_swing(), SwingEngineV8)
    assert isinstance(
        assembly.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV55,
    )
    assert assembly.spec(EngineSlot.SWING).strategy.version == "3.0.0"
    swing = assembly.build_swing()
    assert swing._recovery_enabled is True
    assert swing._recovery_daily_lookback_days == 5
    assert swing._recovery_minimum_reward_risk == Decimal("1.5")


def test_correction_avwap_definition_preserves_v8_and_selects_v9() -> None:
    previous = MarketBotAssembly.from_path(STRUCTURE_RECOVERY_DEFINITION)
    assembly = MarketBotAssembly.from_path(CORRECTION_AVWAP_DEFINITION)

    assert isinstance(previous.build_swing(), SwingEngineV8)
    assert previous.definition.version == "7.24.0"
    assert isinstance(assembly.build_swing(), SwingEngineV9)
    assert assembly.definition.version == "7.25.0"
    assert assembly.spec(EngineSlot.SWING).strategy.version == "3.1.0"


def test_rearmed_recovery_definition_promotes_swing_v10_through_alert_v38() -> None:
    previous = MarketBotAssembly.from_path(CORRECTION_AVWAP_DEFINITION)
    assembly = MarketBotAssembly.from_path(REARMED_RECOVERY_DEFINITION)

    assert isinstance(previous.build_swing(), SwingEngineV9)
    assert isinstance(previous.build_alert(), AlertEngineV37)
    assert previous.definition.version == "7.25.0"
    assert isinstance(assembly.build_swing(), SwingEngineV10)
    assert isinstance(assembly.build_alert(), AlertEngineV38)
    assert assembly.definition.version == "7.26.0"
    assert assembly.spec(EngineSlot.SWING).strategy.version == "3.2.0"
    assert assembly.spec(EngineSlot.ALERT).strategy.version == "1.3.0"
    assert assembly.build_swing()._recovery_selloff_lookback_days == 10


def test_default_definition_declares_every_engine_slot_and_strategy() -> None:
    definition = load_marketbot_definition(DEFINITION)

    assert set(definition.engines) == set(EngineSlot) - {
        EngineSlot.VOLUME_STRUCTURE,
        EngineSlot.OPTIONS_GAMMA,
        EngineSlot.NEWS_INTELLIGENCE,
        EngineSlot.SWING_CHANNEL_4H,
        EngineSlot.GERI_4H,
        EngineSlot.SWING_TRADE,
        EngineSlot.ORDER_FLOW,
        EngineSlot.SCALP,
        EngineSlot.INTRADAY_OPPORTUNITY,
    }
    assert definition.version == "7.2.0"
    assert all(item.strategy.version for item in definition.engines.values())
    assert definition.engines[EngineSlot.INTRADAY].implementation == "4.0.0"
    assert definition.engines[EngineSlot.PORTFOLIO_FLOW].implementation == "2.0.0"
    assert (
        definition.engines[EngineSlot.LONG_PORTFOLIO].strategy.artifact
        == ROOT / "configs/rules/long_portfolio/1.0.0.yaml"
    )


def test_latest_definition_adds_volume_structure_without_mutating_7_2() -> None:
    definition = load_marketbot_definition(VOLUME_STRUCTURE_DEFINITION)

    assert set(definition.engines) == set(EngineSlot) - {
        EngineSlot.OPTIONS_GAMMA,
        EngineSlot.NEWS_INTELLIGENCE,
        EngineSlot.SWING_CHANNEL_4H,
        EngineSlot.GERI_4H,
        EngineSlot.SWING_TRADE,
        EngineSlot.ORDER_FLOW,
        EngineSlot.SCALP,
        EngineSlot.INTRADAY_OPPORTUNITY,
    }
    assert definition.version == "7.3.0"
    assert definition.engines[EngineSlot.VOLUME_STRUCTURE].implementation == "1.0.0"
    assert definition.engines[EngineSlot.ALERT].implementation == "3.4.0"
    assert definition.engines[EngineSlot.SIGNAL_FUSION].implementation == "0.4.0"


def test_latest_definition_activates_invalidation_aware_volume_structure() -> None:
    previous = load_marketbot_definition(VOLUME_STRUCTURE_DEFINITION)
    definition = load_marketbot_definition(INVALIDATION_DEFINITION)

    assert previous.engines[EngineSlot.VOLUME_STRUCTURE].implementation == "1.0.0"
    assert definition.version == "7.4.0"
    assert definition.engines[EngineSlot.VOLUME_STRUCTURE].implementation == "1.1.0"
    assert isinstance(
        MarketBotAssembly(definition).build_volume_structure(),
        VolumeStructureEngineV11,
    )


def test_latest_definition_activates_quality_radar_and_current_maturity() -> None:
    previous = load_marketbot_definition(INVALIDATION_DEFINITION)
    definition = load_marketbot_definition(LATEST_DEFINITION)
    assembly = MarketBotAssembly(definition)

    assert previous.engines[EngineSlot.ENTRY_WATCHER].implementation == "5.1.0"
    assert previous.engines[EngineSlot.ENTRY_OPPORTUNITY].implementation == "2.0.0"
    assert definition.version == "7.5.0"
    assert definition.engines[EngineSlot.ENTRY_WATCHER].implementation == "5.2.0"
    assert definition.engines[EngineSlot.ENTRY_OPPORTUNITY].implementation == "3.0.0"
    assert isinstance(
        assembly.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV52,
    )
    assert isinstance(
        assembly.build_entry_opportunity(store=InMemoryEntryOpportunityStore()),
        EntryOpportunityEngineV3,
    )


def test_gamma_definition_adds_active_producer_and_bounded_consumers() -> None:
    previous = load_marketbot_definition(LATEST_DEFINITION)
    definition = load_marketbot_definition(GAMMA_DEFINITION)
    assembly = MarketBotAssembly(definition)

    assert EngineSlot.OPTIONS_GAMMA not in previous.engines
    assert definition.version == "7.6.0"
    assert definition.engines[EngineSlot.OPTIONS_GAMMA].mode is EngineMode.ACTIVE
    assert isinstance(assembly.build(EngineSlot.OPTIONS_GAMMA), OptionsGammaEngine)
    assert isinstance(assembly.build_alert(), AlertEngineV35)
    assert isinstance(assembly.build_signal_fusion(), SignalFusionEngineV05)


def test_early_radar_definition_preserves_gamma_and_activates_watcher_v53() -> None:
    previous = load_marketbot_definition(GAMMA_DEFINITION)
    definition = load_marketbot_definition(EARLY_RADAR_DEFINITION)
    assembly = MarketBotAssembly(definition)

    assert previous.engines[EngineSlot.ENTRY_WATCHER].implementation == "5.2.0"
    assert definition.version == "7.7.0"
    assert definition.engines[EngineSlot.OPTIONS_GAMMA].mode is EngineMode.ACTIVE
    assert isinstance(
        assembly.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV53,
    )


def test_structural_swing_definition_preserves_radar_and_activates_swing_v5() -> None:
    previous = load_marketbot_definition(EARLY_RADAR_DEFINITION)
    definition = load_marketbot_definition(STRUCTURAL_SWING_DEFINITION)
    assembly = MarketBotAssembly(definition)

    assert previous.engines[EngineSlot.SWING].implementation == "4.0.0"
    assert definition.version == "7.8.0"
    assert definition.engines[EngineSlot.ENTRY_WATCHER].implementation == "5.3.0"
    assert isinstance(assembly.build_swing(), SwingEngineV5)
    assert definition.engines[EngineSlot.SWING].strategy.version == "1.2.0"


def test_pullback_entry_definition_activates_watcher_v54() -> None:
    previous = load_marketbot_definition(STRUCTURAL_SWING_DEFINITION)
    definition = load_marketbot_definition(PULLBACK_ENTRY_DEFINITION)
    assembly = MarketBotAssembly(definition)

    assert previous.engines[EngineSlot.ENTRY_WATCHER].implementation == "5.3.0"
    assert definition.version == "7.9.0"
    assert definition.engines[EngineSlot.ENTRY_WATCHER].strategy.version == "1.3.0"
    assert isinstance(
        assembly.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV54,
    )


def test_operational_modes_select_slots_from_the_definition() -> None:
    definition = load_marketbot_definition(DEFINITION)
    assembly = MarketBotAssembly(definition)

    assert EngineSlot.ENTRY_RECOVERY in assembly.slots_for_mode(EngineMode.ACTIVE)
    assert assembly.slots_for_mode(EngineMode.SCHEDULED) == (EngineSlot.DILUTION_SEC,)
    assert assembly.slots_for_mode(EngineMode.ON_DEMAND) == (EngineSlot.PETER_LYNCH,)


def test_one_assembly_builds_the_core_and_alert_implementations() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)

    assert isinstance(assembly.build_long_term(), LongTermEngineV2)
    assert isinstance(assembly.build_swing(), SwingEngineV4)
    assert isinstance(assembly.build_intraday(), IntradayEngineV4)
    assert isinstance(assembly.build_alert(), AlertEngineV33)
    assert isinstance(
        assembly.build_entry_watcher(store=InMemoryEntryWatchStore()),
        EntryWatcherV5,
    )
    opportunity = assembly.build_entry_opportunity(store=InMemoryEntryOpportunityStore())
    assert isinstance(opportunity, EntryOpportunityEngine)
    assert opportunity.engine_id == "entry-opportunity"
    assert assembly.spec(EngineSlot.ENTRY_OPPORTUNITY).implementation == "2.0.0"
    assert isinstance(assembly.build_portfolio_flow(), PortfolioFlowEngineV2)
    assert isinstance(assembly.build_entry_recovery(), EntryRecoveryEngineV11)


def test_generic_build_api_does_not_require_a_new_assembly_method() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)

    assert isinstance(assembly.build(EngineSlot.LONG_TERM), LongTermEngineV2)
    assert isinstance(assembly.build(EngineSlot.SWING), SwingEngineV4)
    assert isinstance(assembly.build(EngineSlot.INTRADAY), IntradayEngineV4)


def test_artifact_engines_resolve_their_own_runtime_policies() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    allocations = (PortfolioAllocation(symbol="HIMS", weight_percent=Decimal("75.73")),)

    long_policy = assembly.resolve_strategy(
        EngineSlot.LONG_PORTFOLIO,
        allocations=allocations,
    )
    patreon_policy = assembly.resolve_strategy(EngineSlot.PATREON_CAPS)

    assert isinstance(long_policy, LongPortfolioPolicy)
    assert long_policy.allocations == allocations
    assert isinstance(patreon_policy, PatreonCapsPolicy)
    assert patreon_policy.rule_version == "1.1.0"


def test_artifact_engines_build_without_policy_objects_from_compositions() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)
    allocations = (PortfolioAllocation(symbol="HIMS", weight_percent=Decimal("75.73")),)

    long_engine = assembly.build_long_portfolio(allocations=allocations)
    patreon_engine = assembly.build_patreon_caps()

    assert isinstance(long_engine, LongPortfolioEngine)
    assert isinstance(patreon_engine, PatreonCapsEngine)


def test_each_confirmation_engine_loads_its_own_strategy_artifact() -> None:
    assembly = MarketBotAssembly.from_path(DEFINITION)

    swing = assembly.build_swing()
    intraday = assembly.build_intraday()
    watcher = assembly.build_entry_watcher(store=InMemoryEntryWatchStore())

    assert assembly.strategy_artifact(EngineSlot.SWING).parent.name == "swing"
    assert assembly.strategy_artifact(EngineSlot.INTRADAY).parent.name == "intraday"
    assert assembly.strategy_artifact(EngineSlot.ENTRY_WATCHER).parent.name == "entry_watcher"
    assert (
        len(
            {
                assembly.strategy_artifact(EngineSlot.SWING),
                assembly.strategy_artifact(EngineSlot.INTRADAY),
                assembly.strategy_artifact(EngineSlot.ENTRY_WATCHER),
            }
        )
        == 3
    )
    assert swing._strategy_version == "1.1.0"
    assert swing._minimum_reward_risk_to_resistance == Decimal("1.5")
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
    assert alert._minimum_swing_reward_risk_to_resistance == Decimal("1.5")
    assert alert._intraday_mature_gate_required is True


def test_settings_load_the_definition_as_the_primary_source() -> None:
    settings = AppSettings(definition_path=DEFINITION, _env_file=None)

    assembly = MarketBotAssembly.from_settings(settings)

    assert assembly.definition.version == "7.2.0"
    assert isinstance(assembly.build_intraday(), IntradayEngineV4)


def test_previous_immutable_definition_remains_loadable_for_rollback() -> None:
    assembly = MarketBotAssembly.from_path(PREVIOUS_DEFINITION)

    assert assembly.definition.version == "7.1.0"
    assert assembly.build_alert().engine_version == "3.2.0"


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


def test_engine_owned_strategy_validation_still_fails_before_startup(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "intraday-invalid.yaml"
    artifact.write_text(
        """\
rule_version: 1.0.0
behavior:
  minimum_momentum_percent: invalid
  minimum_risk_percent: 0.25
  minimum_atr_risk_multiple: 0.50
  reward_risk_ratio: 1.50
  maximum_trigger_extension_atr: 0.50
  maximum_ema20_extension_atr: 2.00
  strong_confirmation_required: true
  five_minute_higher_low_required: true
""",
        encoding="utf-8",
    )
    definition = load_marketbot_definition(DEFINITION)
    engines = dict(definition.engines)
    engines[EngineSlot.INTRADAY] = replace(
        engines[EngineSlot.INTRADAY],
        strategy=replace(engines[EngineSlot.INTRADAY].strategy, artifact=artifact),
    )

    with pytest.raises(
        ValueError,
        match="strategy behavior minimum_momentum_percent must be decimal",
    ):
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
        "SwingEngineV4",
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


def test_assembly_does_not_own_engine_version_or_business_rule_branches() -> None:
    source = (INTEGRATION / "engine_assembly.py").read_text(encoding="utf-8")

    assert "spec.implementation ==" not in source
    assert "spec.implementation in" not in source
    assert "_validate_confirmation_behavior" not in source
    assert "minimum_momentum_percent" not in source
    assert "fresh_reconfirmation_delay_minutes" not in source


def test_compositions_do_not_load_engine_strategy_artifacts_directly() -> None:
    composition_files = (
        "distributed_composition.py",
        "long_portfolio_composition.py",
        "long_portfolio_monitor.py",
        "patreon_caps_composition.py",
    )

    for filename in composition_files:
        source = (INTEGRATION / filename).read_text(encoding="utf-8")
        assert "load_long_portfolio_policy" not in source
        assert "load_patreon_caps_policy" not in source


def test_registry_rejects_duplicate_engine_registration() -> None:
    registration = EngineRegistration.simple(
        implementations={"1.0.0": object},
        required_since="1.0.0",
    )
    registry = EngineRegistry({EngineSlot.LONG_TERM: registration})

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EngineSlot.LONG_TERM, registration)


def test_required_engine_slots_are_derived_from_registration_metadata() -> None:
    assembly = MarketBotAssembly.from_path(ROOT / "configs/marketbot/6.0.0.yaml")

    assert EngineSlot.ENTRY_RECOVERY not in assembly.definition.engines
    assert EngineSlot.ENTRY_RECOVERY not in assembly.required_slots()

    current = MarketBotAssembly.from_path(DEFINITION)
    assert EngineSlot.ENTRY_RECOVERY in current.required_slots()


def test_definition_model_is_a_separate_public_boundary() -> None:
    definition = load_definition_model(Path("configs/marketbot/7.2.0.yaml"))

    assert DefinitionEngineSlot is EngineSlot
    assert DefinitionEngineMode is EngineMode
    assert definition.engines[EngineSlot.ENTRY_RECOVERY].mode is EngineMode.ACTIVE
