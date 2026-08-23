"""Concrete engine registrations owned by the repository composition root."""

from __future__ import annotations

from app.alert_engine import (
    AlertEngine,
    AlertEngineV2,
    AlertEngineV3,
    AlertEngineV31,
    AlertEngineV32,
    AlertEngineV33,
    AlertEngineV34,
    AlertEngineV35,
    AlertEngineV36,
    AlertEngineV37,
    AlertEngineV38,
)
from app.alert_engine.strategy import configure_engine as configure_alert
from app.alert_engine.strategy import validate_strategy as validate_alert
from app.dilution_sec_engine import DilutionSecEngine
from app.elliott_wave_engine import ElliottWaveEngine
from app.entry_opportunity_engine import (
    EntryOpportunityEngine,
    EntryOpportunityEngineV2,
    EntryOpportunityEngineV3,
    EntryOpportunityEngineV4,
    EntryOpportunityEngineV5,
    EntryOpportunityEngineV6,
)
from app.entry_recovery_engine import EntryRecoveryEngine, EntryRecoveryEngineV11
from app.entry_recovery_engine.strategy import configure_engine as configure_recovery
from app.entry_recovery_engine.strategy import validate_strategy as validate_recovery
from app.entry_watcher import (
    EntryWatcher,
    EntryWatcherV2,
    EntryWatcherV3,
    EntryWatcherV4,
    EntryWatcherV5,
    EntryWatcherV51,
    EntryWatcherV52,
    EntryWatcherV53,
    EntryWatcherV54,
    EntryWatcherV55,
)
from app.entry_watcher.strategy import configure_engine as configure_watcher
from app.entry_watcher.strategy import validate_strategy as validate_watcher
from app.intraday_engine import (
    IntradayEngine,
    IntradayEngineV2,
    IntradayEngineV3,
    IntradayEngineV4,
)
from app.intraday_engine.strategy import configure_engine as configure_intraday
from app.intraday_engine.strategy import validate_strategy as validate_intraday
from app.long_portfolio_engine import LongPortfolioEngine
from app.long_portfolio_engine.strategy import configure_engine as configure_long_portfolio
from app.long_portfolio_engine.strategy import resolve_strategy as resolve_long_portfolio
from app.long_portfolio_engine.strategy import validate_strategy as validate_long_portfolio
from app.long_term_engine import LongTermEngine, LongTermEngineV2
from app.market_rotation_engine import RotationEngine
from app.news_intelligence_engine import NewsIntelligenceEngine
from app.options_gamma_engine import OptionsGammaEngine
from app.patreon_caps_engine import PatreonCapsEngine
from app.patreon_caps_engine.strategy import configure_engine as configure_patreon_caps
from app.patreon_caps_engine.strategy import resolve_strategy as resolve_patreon_caps
from app.patreon_caps_engine.strategy import validate_strategy as validate_patreon_caps
from app.peter_lynch_engine import PeterLynchEngine
from app.portfolio_flow_engine import PortfolioFlowEngineV1, PortfolioFlowEngineV2
from app.portfolio_flow_engine.strategy import configure_engine as configure_portfolio_flow
from app.portfolio_flow_engine.strategy import validate_strategy as validate_portfolio_flow
from app.signal_fusion_engine import (
    SignalFusionEngine,
    SignalFusionEngineV04,
    SignalFusionEngineV05,
)
from app.support_confirmation_engine import SupportConfirmationEngine
from app.swing_4h_geri_engine import (
    Swing4HGeriEngine,
    Swing4HGeriEngineV11,
    Swing4HGeriEngineV12,
    Swing4HGeriEngineV13,
    Swing4HGeriEngineV14,
    Swing4HGeriEngineV15,
)
from app.swing_4h_geri_engine.strategy import configure_engine as configure_geri_4h
from app.swing_4h_geri_engine.strategy import validate_strategy as validate_geri_4h
from app.swing_channel_4h_engine import SwingChannel4HEngine, SwingChannel4HEngineV11
from app.swing_engine import (
    SwingEngine,
    SwingEngineV2,
    SwingEngineV3,
    SwingEngineV4,
    SwingEngineV5,
    SwingEngineV6,
    SwingEngineV7,
    SwingEngineV8,
    SwingEngineV9,
    SwingEngineV10,
    SwingEngineV11,
)
from app.swing_engine.strategy import configure_engine as configure_swing
from app.swing_engine.strategy import validate_strategy as validate_swing
from app.swing_trade_engine import SwingTradeEngine, SwingTradeEngineV11, SwingTradeEngineV12
from app.swing_trade_engine.strategy import configure_engine as configure_swing_trade
from app.swing_trade_engine.strategy import validate_strategy as validate_swing_trade
from app.volume_structure_engine import VolumeStructureEngine, VolumeStructureEngineV11

from .engine_registry import EngineRegistration, EngineRegistry
from .marketbot_definition import EngineSlot


def default_engine_registry() -> EngineRegistry:
    """Return a fresh registry so tests and alternate roots cannot mutate a global."""

    simple = EngineRegistration.simple
    return EngineRegistry(
        {
            EngineSlot.LONG_TERM: simple(
                implementations={"1.1.1": LongTermEngine, "2.0.0": LongTermEngineV2}
            ),
            EngineSlot.SWING: EngineRegistration(
                implementations={
                    "1.1.1": SwingEngine,
                    "2.0.0": SwingEngineV2,
                    "3.0.0": SwingEngineV3,
                    "4.0.0": SwingEngineV4,
                    "5.0.0": SwingEngineV5,
                    "6.0.0": SwingEngineV6,
                    "7.0.0": SwingEngineV7,
                    "8.0.0": SwingEngineV8,
                    "9.0.0": SwingEngineV9,
                    "10.0.0": SwingEngineV10,
                    "11.0.0": SwingEngineV11,
                },
                required_since="0.0.0",
                configure=configure_swing,
                validate_strategy=validate_swing,
            ),
            EngineSlot.SWING_CHANNEL_4H: simple(
                implementations={
                    "1.0.0": SwingChannel4HEngine,
                    "1.1.0": SwingChannel4HEngineV11,
                },
                required_since="7.14.0",
            ),
            EngineSlot.GERI_4H: EngineRegistration(
                implementations={
                    "1.0.0": Swing4HGeriEngine,
                    "1.1.0": Swing4HGeriEngineV11,
                    "1.2.0": Swing4HGeriEngineV12,
                    "1.3.0": Swing4HGeriEngineV13,
                    "1.4.0": Swing4HGeriEngineV14,
                    "1.5.0": Swing4HGeriEngineV15,
                },
                required_since="7.15.0",
                configure=configure_geri_4h,
                validate_strategy=validate_geri_4h,
            ),
            EngineSlot.SWING_TRADE: EngineRegistration(
                implementations={
                    "1.0.0": SwingTradeEngine,
                    "1.1.0": SwingTradeEngineV11,
                    "1.2.0": SwingTradeEngineV12,
                },
                required_since="7.20.0",
                configure=configure_swing_trade,
                validate_strategy=validate_swing_trade,
            ),
            EngineSlot.INTRADAY: EngineRegistration(
                implementations={
                    "1.0.0": IntradayEngine,
                    "2.0.0": IntradayEngineV2,
                    "3.0.0": IntradayEngineV3,
                    "4.0.0": IntradayEngineV4,
                },
                required_since="0.0.0",
                configure=configure_intraday,
                validate_strategy=validate_intraday,
            ),
            EngineSlot.ENTRY_WATCHER: EngineRegistration(
                implementations={
                    "1.0.0": EntryWatcher,
                    "2.0.0": EntryWatcherV2,
                    "3.0.0": EntryWatcherV3,
                    "4.0.0": EntryWatcherV4,
                    "5.0.0": EntryWatcherV5,
                    "5.1.0": EntryWatcherV51,
                    "5.2.0": EntryWatcherV52,
                    "5.3.0": EntryWatcherV53,
                    "5.4.0": EntryWatcherV54,
                    "5.5.0": EntryWatcherV55,
                },
                required_since="0.0.0",
                configure=configure_watcher,
                validate_strategy=validate_watcher,
            ),
            EngineSlot.ENTRY_OPPORTUNITY: simple(
                implementations={
                    "1.0.0": EntryOpportunityEngine,
                    "2.0.0": EntryOpportunityEngineV2,
                    "3.0.0": EntryOpportunityEngineV3,
                    "4.0.0": EntryOpportunityEngineV4,
                    "5.0.0": EntryOpportunityEngineV5,
                    "6.0.0": EntryOpportunityEngineV6,
                }
            ),
            EngineSlot.ENTRY_RECOVERY: EngineRegistration(
                implementations={
                    "1.0.0": EntryRecoveryEngine,
                    "1.1.0": EntryRecoveryEngineV11,
                },
                required_since="7.0.0",
                configure=configure_recovery,
                validate_strategy=validate_recovery,
            ),
            EngineSlot.ALERT: EngineRegistration(
                implementations={
                    "1.0.0": AlertEngine,
                    "2.0.0": AlertEngineV2,
                    "3.0.0": AlertEngineV3,
                    "3.1.0": AlertEngineV31,
                    "3.2.0": AlertEngineV32,
                    "3.3.0": AlertEngineV33,
                    "3.4.0": AlertEngineV34,
                    "3.5.0": AlertEngineV35,
                    "3.6.0": AlertEngineV36,
                    "3.7.0": AlertEngineV37,
                    "3.8.0": AlertEngineV38,
                },
                required_since="0.0.0",
                configure=configure_alert,
                validate_strategy=validate_alert,
            ),
            EngineSlot.MARKET_ROTATION: simple(implementations={"1.0.0": RotationEngine}),
            EngineSlot.PORTFOLIO_FLOW: EngineRegistration(
                implementations={
                    "1.0.0": PortfolioFlowEngineV1,
                    "2.0.0": PortfolioFlowEngineV2,
                },
                required_since="0.0.0",
                configure=configure_portfolio_flow,
                validate_strategy=validate_portfolio_flow,
            ),
            EngineSlot.LONG_PORTFOLIO: EngineRegistration(
                implementations={"1.0.0": LongPortfolioEngine},
                required_since="0.0.0",
                configure=configure_long_portfolio,
                validate_strategy=validate_long_portfolio,
                strategy_resolver=resolve_long_portfolio,
            ),
            EngineSlot.PATREON_CAPS: EngineRegistration(
                implementations={"1.0.0": PatreonCapsEngine},
                required_since="0.0.0",
                configure=configure_patreon_caps,
                validate_strategy=validate_patreon_caps,
                strategy_resolver=resolve_patreon_caps,
            ),
            EngineSlot.ELLIOTT_WAVE: simple(implementations={"0.1.0": ElliottWaveEngine}),
            EngineSlot.SUPPORT_CONFIRMATION: simple(
                implementations={"0.2.0": SupportConfirmationEngine}
            ),
            EngineSlot.VOLUME_STRUCTURE: simple(
                implementations={
                    "1.0.0": VolumeStructureEngine,
                    "1.1.0": VolumeStructureEngineV11,
                },
                required_since="7.3.0",
            ),
            EngineSlot.OPTIONS_GAMMA: simple(
                implementations={"1.0.0": OptionsGammaEngine},
                required_since="7.6.0",
            ),
            EngineSlot.SIGNAL_FUSION: simple(
                implementations={
                    "0.3.0": SignalFusionEngine,
                    "0.4.0": SignalFusionEngineV04,
                    "0.5.0": SignalFusionEngineV05,
                }
            ),
            EngineSlot.DILUTION_SEC: simple(implementations={"1.0.0": DilutionSecEngine}),
            EngineSlot.PETER_LYNCH: simple(implementations={"1.1.0": PeterLynchEngine}),
            EngineSlot.NEWS_INTELLIGENCE: simple(
                implementations={"1.0.0": NewsIntelligenceEngine},
                required_since="7.12.0",
            ),
        }
    )
