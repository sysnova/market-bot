"""Single versioned assembly point for every operational MarketBot engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from app.common.settings import AppSettings

from .engine_catalog import default_engine_registry
from .engine_registry import EngineFactory, EngineRegistration, EngineRegistry
from .marketbot_definition import (
    EngineMode,
    EngineSlot,
    EngineSpec,
    EngineStrategy,
    MarketBotDefinition,
    StrategyKind,
    load_configured_marketbot_definition,
    load_marketbot_definition,
    require_exact_semver,
    validate_engine_strategy,
)

if TYPE_CHECKING:
    from app.alert_engine import AlertEngine, AlertEngineV3State
    from app.dilution_sec_engine import DilutionSecEngine
    from app.elliott_wave_engine import ElliottWaveEngine
    from app.entry_opportunity_engine import EntryOpportunityEngine, EntryOpportunityStore
    from app.entry_recovery_engine import EntryRecoveryEngine
    from app.entry_watcher import EntryWatcher, EntryWatcherPolicy
    from app.entry_watcher.ports import EntryWatchStore
    from app.intraday_engine import IntradayEngine
    from app.long_portfolio_engine import (
        LongPortfolioEngine,
        LongPortfolioState,
        PortfolioAllocation,
    )
    from app.long_term_engine import LongTermEngine
    from app.market_rotation_engine import RotationEngine
    from app.options_gamma_engine import OptionsGammaEngine
    from app.patreon_caps_engine import PatreonCapsEngine, PatreonCapsWatch
    from app.peter_lynch_engine import PeterLynchEngine
    from app.portfolio_flow_engine import PortfolioFlowEngineV1
    from app.signal_fusion_engine import SignalFusionEngine
    from app.support_confirmation_engine import SupportConfirmationEngine
    from app.swing_4h_geri_engine import Swing4HGeriEngine
    from app.swing_channel_4h_engine import SwingChannel4HEngine
    from app.swing_engine import SwingEngine
    from app.volume_structure_engine import VolumeStructureEngine

__all__ = [
    "EngineMode",
    "EngineSlot",
    "EngineSpec",
    "EngineStrategy",
    "MarketBotAssembly",
    "MarketBotDefinition",
    "StrategyKind",
    "load_marketbot_definition",
]

LegacyCatalog = Mapping[EngineSlot, Mapping[str, EngineFactory]]


class MarketBotAssembly:
    """Validate one definition and construct engines through registered adapters."""

    def __init__(
        self,
        definition: MarketBotDefinition,
        *,
        catalog: EngineRegistry | LegacyCatalog | None = None,
    ) -> None:
        self.definition = definition
        self._registry = _as_registry(catalog)
        self._validate()

    @classmethod
    def from_path(cls, path: Path) -> MarketBotAssembly:
        return cls(load_marketbot_definition(path))

    @classmethod
    def from_settings(cls, settings: AppSettings) -> MarketBotAssembly:
        """Load the declared assembly, honoring the legacy confirmation rollback knob."""

        return cls(load_configured_marketbot_definition(settings))

    def spec(self, slot: EngineSlot) -> EngineSpec:
        return self.definition.engines[slot]

    def required_slots(self) -> frozenset[EngineSlot]:
        """Return requirements applicable to this definition schema version."""

        return self._registry.required_slots(self.definition.version)

    def slots_for_mode(self, mode: EngineMode) -> tuple[EngineSlot, ...]:
        """Return slots participating in one operational lifecycle mode."""

        return tuple(
            slot for slot, spec in self.definition.engines.items() if spec.mode is mode
        )

    def strategy_artifact(self, slot: EngineSlot) -> Path:
        artifact = self.spec(slot).strategy.artifact
        if artifact is None:
            raise ValueError(f"engine {slot.value} uses an embedded strategy")
        return artifact

    def resolve_strategy(
        self,
        slot: EngineSlot,
        *,
        artifact_override: Path | None = None,
        **context: object,
    ) -> object:
        """Resolve an engine-owned policy without parsing its artifact in a composition."""

        return self._registry.registration(slot).resolve_strategy(
            self.spec(slot),
            artifact_override=artifact_override,
            **context,
        )

    def build(
        self,
        slot: EngineSlot,
        *args: object,
        strategy_artifact_override: Path | None = None,
        **kwargs: object,
    ) -> object:
        """Build any registered engine without extending this class for each new version."""

        return self._registry.registration(slot).build(
            self.spec(slot),
            *args,
            strategy_artifact_override=strategy_artifact_override,
            **kwargs,
        )

    # Typed compatibility methods keep existing composition roots stable. New generic
    # integrations can call build(slot, ...) directly.
    def build_long_term(self) -> LongTermEngine:
        return cast("LongTermEngine", self.build(EngineSlot.LONG_TERM))

    def build_swing(self) -> SwingEngine:
        return cast("SwingEngine", self.build(EngineSlot.SWING))

    def build_swing_channel_4h(self) -> SwingChannel4HEngine:
        return cast(
            "SwingChannel4HEngine", self.build(EngineSlot.SWING_CHANNEL_4H)
        )

    def build_4hgeri(self) -> Swing4HGeriEngine:
        return cast("Swing4HGeriEngine", self.build(EngineSlot.GERI_4H))

    def build_intraday(self) -> IntradayEngine:
        return cast("IntradayEngine", self.build(EngineSlot.INTRADAY))

    def build_entry_watcher(
        self,
        *,
        store: EntryWatchStore,
        policy: EntryWatcherPolicy | None = None,
    ) -> EntryWatcher:
        return cast(
            "EntryWatcher",
            self.build(EngineSlot.ENTRY_WATCHER, store=store, policy=policy),
        )

    def build_alert(
        self,
        *,
        restored_state: AlertEngineV3State | None = None,
    ) -> AlertEngine:
        return cast(
            "AlertEngine",
            self.build(EngineSlot.ALERT, restored_state=restored_state),
        )

    def build_entry_opportunity(
        self,
        *,
        store: EntryOpportunityStore,
    ) -> EntryOpportunityEngine:
        return cast(
            "EntryOpportunityEngine",
            self.build(EngineSlot.ENTRY_OPPORTUNITY, store=store),
        )

    def build_entry_recovery(self) -> EntryRecoveryEngine:
        return cast("EntryRecoveryEngine", self.build(EngineSlot.ENTRY_RECOVERY))

    def build_market_rotation(self) -> RotationEngine:
        return cast("RotationEngine", self.build(EngineSlot.MARKET_ROTATION))

    def build_portfolio_flow(self) -> PortfolioFlowEngineV1:
        return cast("PortfolioFlowEngineV1", self.build(EngineSlot.PORTFOLIO_FLOW))

    def build_long_portfolio(
        self,
        *,
        allocations: tuple[PortfolioAllocation, ...],
        restored_states: Iterable[LongPortfolioState] = (),
        strategy_artifact_override: Path | None = None,
    ) -> LongPortfolioEngine:
        return cast(
            "LongPortfolioEngine",
            self.build(
                EngineSlot.LONG_PORTFOLIO,
                allocations=allocations,
                restored_states=restored_states,
                strategy_artifact_override=strategy_artifact_override,
            ),
        )

    def build_patreon_caps(
        self,
        *,
        restored_watches: tuple[PatreonCapsWatch, ...] = (),
        strategy_artifact_override: Path | None = None,
    ) -> PatreonCapsEngine:
        return cast(
            "PatreonCapsEngine",
            self.build(
                EngineSlot.PATREON_CAPS,
                restored_watches=restored_watches,
                strategy_artifact_override=strategy_artifact_override,
            ),
        )

    def build_elliott_wave(self) -> ElliottWaveEngine:
        return cast("ElliottWaveEngine", self.build(EngineSlot.ELLIOTT_WAVE))

    def build_support_confirmation(self) -> SupportConfirmationEngine:
        return cast(
            "SupportConfirmationEngine",
            self.build(EngineSlot.SUPPORT_CONFIRMATION),
        )

    def build_volume_structure(self) -> VolumeStructureEngine:
        return cast(
            "VolumeStructureEngine",
            self.build(EngineSlot.VOLUME_STRUCTURE),
        )

    def build_options_gamma(self) -> OptionsGammaEngine:
        return cast("OptionsGammaEngine", self.build(EngineSlot.OPTIONS_GAMMA))

    def build_signal_fusion(self) -> SignalFusionEngine:
        return cast("SignalFusionEngine", self.build(EngineSlot.SIGNAL_FUSION))

    def build_dilution_sec(self) -> DilutionSecEngine:
        return cast("DilutionSecEngine", self.build(EngineSlot.DILUTION_SEC))

    def build_peter_lynch(self) -> PeterLynchEngine:
        return cast("PeterLynchEngine", self.build(EngineSlot.PETER_LYNCH))

    def _validate(self) -> None:
        if self.definition.definition_id != "marketbot":
            raise ValueError("definition_id must be marketbot")
        require_exact_semver(self.definition.version, "definition version")
        missing = self.required_slots() - set(self.definition.engines)
        extra = set(self.definition.engines) - self._registry.slots()
        if missing or extra:
            raise ValueError(
                f"definition engine slots mismatch; missing={missing}, extra={extra}"
            )
        for slot, spec in self.definition.engines.items():
            require_exact_semver(spec.implementation, f"{slot.value} implementation")
            require_exact_semver(spec.strategy.version, f"{slot.value} strategy")
            registration = self._registry.registration(slot)
            if spec.implementation not in registration.implementations:
                raise ValueError(
                    f"unregistered implementation: {slot.value}@{spec.implementation}"
                )
            validate_engine_strategy(slot, spec.strategy)
            registration.validate(spec)


def _as_registry(catalog: EngineRegistry | LegacyCatalog | None) -> EngineRegistry:
    if catalog is None:
        return default_engine_registry()
    if isinstance(catalog, EngineRegistry):
        return catalog
    return EngineRegistry(
        {
            slot: EngineRegistration.simple(implementations=implementations)
            for slot, implementations in catalog.items()
        }
    )
