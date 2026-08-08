"""Single versioned assembly point for every operational MarketBot engine."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml

from app.alert_engine import AlertEngine, AlertEngineV2, AlertEngineV3
from app.common.settings import AppSettings
from app.dilution_sec_engine import DilutionSecEngine
from app.elliott_wave_engine import ElliottWaveEngine
from app.entry_opportunity_engine import EntryOpportunityEngine, EntryOpportunityStore
from app.entry_watcher import (
    EntryWatcher,
    EntryWatcherPolicy,
    EntryWatcherV2,
    EntryWatcherV3,
    EntryWatcherV4,
    EntryWatcherV5,
)
from app.entry_watcher.ports import EntryWatchStore
from app.intraday_engine import (
    IntradayEngine,
    IntradayEngineV2,
    IntradayEngineV3,
    IntradayEngineV4,
)
from app.long_portfolio_engine import LongPortfolioEngine, LongPortfolioPolicy, LongPortfolioState
from app.long_term_engine import LongTermEngine, LongTermEngineV2
from app.market_rotation_engine import RotationEngine
from app.patreon_caps_engine import PatreonCapsEngine, PatreonCapsPolicy, PatreonCapsWatch
from app.peter_lynch_engine import PeterLynchEngine
from app.portfolio_flow_engine import (
    PortfolioFlowEngineV1,
    PortfolioFlowEngineV2,
    load_portfolio_flow_policy,
)
from app.signal_fusion_engine import SignalFusionEngine
from app.support_confirmation_engine import SupportConfirmationEngine
from app.swing_engine import SwingEngine, SwingEngineV2, SwingEngineV3

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class EngineSlot(StrEnum):
    LONG_TERM = "long-term"
    SWING = "swing"
    INTRADAY = "intraday"
    ENTRY_WATCHER = "entry-watcher"
    ENTRY_OPPORTUNITY = "entry-opportunity"
    ALERT = "alert"
    MARKET_ROTATION = "market-rotation"
    PORTFOLIO_FLOW = "portfolio-flow"
    LONG_PORTFOLIO = "long-portfolio"
    PATREON_CAPS = "patreon-caps"
    ELLIOTT_WAVE = "elliott-wave"
    SUPPORT_CONFIRMATION = "support-confirmation"
    SIGNAL_FUSION = "signal-fusion"
    DILUTION_SEC = "dilution-sec"
    PETER_LYNCH = "peter-lynch"


class StrategyKind(StrEnum):
    EMBEDDED = "embedded"
    ARTIFACT = "artifact"


class EngineMode(StrEnum):
    ACTIVE = "active"
    SCHEDULED = "scheduled"
    ON_DEMAND = "on-demand"


@dataclass(frozen=True, slots=True)
class EngineStrategy:
    kind: StrategyKind
    version: str
    artifact: Path | None = None


@dataclass(frozen=True, slots=True)
class EngineSpec:
    implementation: str
    mode: EngineMode
    strategy: EngineStrategy


@dataclass(frozen=True, slots=True)
class MarketBotDefinition:
    definition_id: str
    version: str
    engines: dict[EngineSlot, EngineSpec]
    source: Path


EngineFactory = Callable[..., object]

_DEFAULT_CATALOG: dict[EngineSlot, dict[str, EngineFactory]] = {
    EngineSlot.LONG_TERM: {
        "1.1.1": LongTermEngine,
        "2.0.0": LongTermEngineV2,
    },
    EngineSlot.SWING: {
        "1.1.1": SwingEngine,
        "2.0.0": SwingEngineV2,
        "3.0.0": SwingEngineV3,
    },
    EngineSlot.INTRADAY: {
        "1.0.0": IntradayEngine,
        "2.0.0": IntradayEngineV2,
        "3.0.0": IntradayEngineV3,
        "4.0.0": IntradayEngineV4,
    },
    EngineSlot.ENTRY_WATCHER: {
        "1.0.0": EntryWatcher,
        "2.0.0": EntryWatcherV2,
        "3.0.0": EntryWatcherV3,
        "4.0.0": EntryWatcherV4,
        "5.0.0": EntryWatcherV5,
    },
    EngineSlot.ENTRY_OPPORTUNITY: {"1.0.0": EntryOpportunityEngine},
    EngineSlot.ALERT: {
        "1.0.0": AlertEngine,
        "2.0.0": AlertEngineV2,
        "3.0.0": AlertEngineV3,
    },
    EngineSlot.MARKET_ROTATION: {"1.0.0": RotationEngine},
    EngineSlot.PORTFOLIO_FLOW: {
        "1.0.0": PortfolioFlowEngineV1,
        "2.0.0": PortfolioFlowEngineV2,
    },
    EngineSlot.LONG_PORTFOLIO: {"1.0.0": LongPortfolioEngine},
    EngineSlot.PATREON_CAPS: {"1.0.0": PatreonCapsEngine},
    EngineSlot.ELLIOTT_WAVE: {"0.1.0": ElliottWaveEngine},
    EngineSlot.SUPPORT_CONFIRMATION: {"0.2.0": SupportConfirmationEngine},
    EngineSlot.SIGNAL_FUSION: {"0.3.0": SignalFusionEngine},
    EngineSlot.DILUTION_SEC: {"1.0.0": DilutionSecEngine},
    EngineSlot.PETER_LYNCH: {"1.1.0": PeterLynchEngine},
}

def load_marketbot_definition(path: Path) -> MarketBotDefinition:
    source = path.resolve()
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("MarketBot definition must be a mapping")
    raw = cast("dict[str, object]", payload)
    raw_engines = _mapping(raw, "engines")
    engines: dict[EngineSlot, EngineSpec] = {}
    for raw_slot, raw_spec in raw_engines.items():
        slot = EngineSlot(raw_slot)
        if not isinstance(raw_spec, dict):
            raise ValueError(f"engine {slot.value} must be a mapping")
        spec = cast("dict[str, object]", raw_spec)
        raw_strategy = _mapping(spec, "strategy")
        artifact_value = raw_strategy.get("artifact")
        artifact = (
            (source.parent / str(artifact_value)).resolve() if artifact_value is not None else None
        )
        engines[slot] = EngineSpec(
            implementation=str(spec["implementation"]),
            mode=EngineMode(str(spec["mode"])),
            strategy=EngineStrategy(
                kind=StrategyKind(str(raw_strategy["kind"])),
                version=str(raw_strategy["version"]),
                artifact=artifact,
            ),
        )
    return MarketBotDefinition(
        definition_id=str(raw["definition_id"]),
        version=str(raw["version"]),
        engines=engines,
        source=source,
    )


class MarketBotAssembly:
    """Validate one definition and construct every engine from one catalog."""

    def __init__(
        self,
        definition: MarketBotDefinition,
        *,
        catalog: dict[EngineSlot, dict[str, EngineFactory]] | None = None,
    ) -> None:
        self.definition = definition
        self._catalog = catalog or _DEFAULT_CATALOG
        self._validate()

    @classmethod
    def from_path(cls, path: Path) -> MarketBotAssembly:
        return cls(load_marketbot_definition(path))

    @classmethod
    def from_settings(cls, settings: AppSettings) -> MarketBotAssembly:
        """Load the declared assembly, honoring the legacy confirmation rollback knob."""

        definition = load_marketbot_definition(settings.definition_path)
        override = settings.entry_confirmation_rule_version
        if override is not None:
            definition = _with_confirmation_override(definition, override)
        return cls(definition)

    def spec(self, slot: EngineSlot) -> EngineSpec:
        return self.definition.engines[slot]

    def strategy_artifact(self, slot: EngineSlot) -> Path:
        artifact = self.spec(slot).strategy.artifact
        if artifact is None:
            raise ValueError(f"engine {slot.value} uses an embedded strategy")
        return artifact

    def build_long_term(self) -> LongTermEngine:
        return cast("LongTermEngine", self._create(EngineSlot.LONG_TERM))

    def build_swing(self) -> SwingEngine:
        spec = self.spec(EngineSlot.SWING)
        options: dict[str, object] = {}
        if spec.implementation == "3.0.0":
            behavior = self._strategy_behavior(EngineSlot.SWING)
            options = {
                "anchored_vwap_gate": _bool_value(behavior, "anchored_vwap_gate"),
                "strategy_version": spec.strategy.version,
            }
        return cast("SwingEngine", self._create(EngineSlot.SWING, **options))

    def build_intraday(self) -> IntradayEngine:
        spec = self.spec(EngineSlot.INTRADAY)
        options: dict[str, object] = {}
        behavior: dict[str, object] = {}
        if spec.implementation in {"3.0.0", "4.0.0"}:
            behavior = self._strategy_behavior(EngineSlot.INTRADAY)
            options = {
                "minimum_momentum_percent": _decimal_value(
                    behavior, "minimum_momentum_percent"
                ),
                "minimum_risk_percent": _decimal_value(behavior, "minimum_risk_percent"),
                "minimum_atr_risk_multiple": _decimal_value(
                    behavior, "minimum_atr_risk_multiple"
                ),
                "reward_risk_ratio": _decimal_value(behavior, "reward_risk_ratio"),
                "strategy_version": spec.strategy.version,
            }
        if spec.implementation == "4.0.0":
            options.update(
                maximum_trigger_extension_atr=_decimal_value(
                    behavior, "maximum_trigger_extension_atr"
                ),
                maximum_ema20_extension_atr=_decimal_value(
                    behavior, "maximum_ema20_extension_atr"
                ),
                strong_confirmation_required=_bool_value(
                    behavior, "strong_confirmation_required"
                ),
                five_minute_higher_low_required=_bool_value(
                    behavior, "five_minute_higher_low_required"
                ),
            )
        return cast("IntradayEngine", self._create(EngineSlot.INTRADAY, **options))

    def build_entry_watcher(
        self,
        *,
        store: EntryWatchStore,
        policy: EntryWatcherPolicy | None = None,
    ) -> EntryWatcher:
        spec = self.spec(EngineSlot.ENTRY_WATCHER)
        options: dict[str, object] = {} if policy is None else {"policy": policy}
        if spec.implementation in {"4.0.0", "5.0.0"}:
            behavior = self._strategy_behavior(EngineSlot.ENTRY_WATCHER)
            resolved_policy = policy or EntryWatcherPolicy()
            resolved_policy = replace(
                resolved_policy,
                trigger_rearm_cooldown=timedelta(
                    minutes=_int_value(behavior, "trigger_rearm_cooldown_minutes")
                ),
            )
            options = {
                "policy": resolved_policy,
                "minimum_reconfirmation_delay": timedelta(
                    minutes=_int_value(behavior, "fresh_reconfirmation_delay_minutes")
                ),
                "strong_confirmation_required": _bool_value(
                    behavior, "strong_confirmation_required"
                ),
                "five_minute_higher_low_required": _bool_value(
                    behavior, "five_minute_higher_low_required"
                ),
            }
            if spec.implementation == "5.0.0":
                options["no_retest_higher_low_enabled"] = _bool_value(
                    behavior, "no_retest_higher_low_continuation"
                )
        return cast(
            "EntryWatcher",
            self._create(EngineSlot.ENTRY_WATCHER, store=store, **options),
        )

    def build_alert(self) -> AlertEngine:
        spec = self.spec(EngineSlot.ALERT)
        options: dict[str, object] = {}
        if spec.implementation == "3.0.0":
            behavior = self._strategy_behavior(EngineSlot.ALERT)
            options = {
                "minimum_reconfirmation_delay": timedelta(
                    minutes=_int_value(behavior, "fresh_reconfirmation_delay_minutes")
                ),
                "strong_confirmation_required": _bool_value(
                    behavior, "strong_confirmation_required"
                ),
                "five_minute_higher_low_required": _bool_value(
                    behavior, "five_minute_higher_low_required"
                ),
                "same_market_session_required": _bool_value(
                    behavior, "same_market_session_required"
                ),
            }
        return cast("AlertEngine", self._create(EngineSlot.ALERT, **options))

    def build_entry_opportunity(
        self,
        *,
        store: EntryOpportunityStore,
    ) -> EntryOpportunityEngine:
        return cast(
            "EntryOpportunityEngine",
            self._create(EngineSlot.ENTRY_OPPORTUNITY, store=store),
        )

    def build_market_rotation(self) -> RotationEngine:
        return cast("RotationEngine", self._create(EngineSlot.MARKET_ROTATION))

    def build_portfolio_flow(self) -> PortfolioFlowEngineV1:
        policy = load_portfolio_flow_policy(self.strategy_artifact(EngineSlot.PORTFOLIO_FLOW))
        return cast(
            "PortfolioFlowEngineV1",
            self._create(EngineSlot.PORTFOLIO_FLOW, policy=policy),
        )

    def build_long_portfolio(
        self,
        policy: LongPortfolioPolicy,
        *,
        restored_states: Iterable[LongPortfolioState] = (),
    ) -> LongPortfolioEngine:
        return cast(
            "LongPortfolioEngine",
            self._create(
                EngineSlot.LONG_PORTFOLIO,
                policy,
                restored_states=restored_states,
            ),
        )

    def build_patreon_caps(
        self,
        policy: PatreonCapsPolicy,
        *,
        restored_watches: tuple[PatreonCapsWatch, ...] = (),
    ) -> PatreonCapsEngine:
        return cast(
            "PatreonCapsEngine",
            self._create(
                EngineSlot.PATREON_CAPS,
                policy,
                restored_watches=restored_watches,
            ),
        )

    def build_elliott_wave(self) -> ElliottWaveEngine:
        return cast("ElliottWaveEngine", self._create(EngineSlot.ELLIOTT_WAVE))

    def build_support_confirmation(self) -> SupportConfirmationEngine:
        return cast(
            "SupportConfirmationEngine",
            self._create(EngineSlot.SUPPORT_CONFIRMATION),
        )

    def build_signal_fusion(self) -> SignalFusionEngine:
        return cast("SignalFusionEngine", self._create(EngineSlot.SIGNAL_FUSION))

    def build_dilution_sec(self) -> DilutionSecEngine:
        return cast("DilutionSecEngine", self._create(EngineSlot.DILUTION_SEC))

    def build_peter_lynch(self) -> PeterLynchEngine:
        return cast("PeterLynchEngine", self._create(EngineSlot.PETER_LYNCH))

    def _create(self, slot: EngineSlot, *args: object, **kwargs: object) -> object:
        spec = self.spec(slot)
        return self._catalog[slot][spec.implementation](*args, **kwargs)

    def _strategy_behavior(self, slot: EngineSlot) -> dict[str, object]:
        artifact = self.strategy_artifact(slot)
        payload = yaml.safe_load(artifact.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"strategy artifact for {slot.value} must be a mapping")
        return _mapping(cast("dict[str, object]", payload), "behavior")

    def _validate(self) -> None:
        if self.definition.definition_id != "marketbot":
            raise ValueError("definition_id must be marketbot")
        _require_semver(self.definition.version, "definition version")
        missing = set(EngineSlot) - set(self.definition.engines)
        extra = set(self.definition.engines) - set(EngineSlot)
        if missing or extra:
            raise ValueError(f"definition engine slots mismatch; missing={missing}, extra={extra}")
        for slot, spec in self.definition.engines.items():
            _require_semver(spec.implementation, f"{slot.value} implementation")
            _require_semver(spec.strategy.version, f"{slot.value} strategy")
            if spec.implementation not in self._catalog.get(slot, {}):
                raise ValueError(f"unregistered implementation: {slot.value}@{spec.implementation}")
            _validate_strategy(slot, spec.strategy)
        self._validate_confirmation_behavior()

    def _validate_confirmation_behavior(self) -> None:
        alert = self.spec(EngineSlot.ALERT)
        if alert.implementation == "3.0.0":
            behavior = self._strategy_behavior(EngineSlot.ALERT)
            _int_value(behavior, "fresh_reconfirmation_delay_minutes")
            _bool_value(behavior, "strong_confirmation_required")
            _bool_value(behavior, "five_minute_higher_low_required")
            _bool_value(behavior, "same_market_session_required")
        swing = self.spec(EngineSlot.SWING)
        if swing.implementation == "3.0.0":
            _bool_value(self._strategy_behavior(EngineSlot.SWING), "anchored_vwap_gate")
        intraday = self.spec(EngineSlot.INTRADAY)
        behavior: dict[str, object] = {}
        if intraday.implementation in {"3.0.0", "4.0.0"}:
            behavior = self._strategy_behavior(EngineSlot.INTRADAY)
            for key in (
                "minimum_momentum_percent",
                "minimum_risk_percent",
                "minimum_atr_risk_multiple",
                "reward_risk_ratio",
            ):
                _decimal_value(behavior, key)
        if intraday.implementation == "4.0.0":
            for key in (
                "maximum_trigger_extension_atr",
                "maximum_ema20_extension_atr",
            ):
                _decimal_value(behavior, key)
            for key in (
                "strong_confirmation_required",
                "five_minute_higher_low_required",
            ):
                _bool_value(behavior, key)
        watcher = self.spec(EngineSlot.ENTRY_WATCHER)
        if watcher.implementation in {"4.0.0", "5.0.0"}:
            behavior = self._strategy_behavior(EngineSlot.ENTRY_WATCHER)
            _int_value(behavior, "fresh_reconfirmation_delay_minutes")
            _int_value(behavior, "trigger_rearm_cooldown_minutes")
            _bool_value(behavior, "strong_confirmation_required")
            _bool_value(behavior, "five_minute_higher_low_required")
            if watcher.implementation == "5.0.0":
                _bool_value(behavior, "no_retest_higher_low_continuation")


def _validate_strategy(slot: EngineSlot, strategy: EngineStrategy) -> None:
    if strategy.kind is StrategyKind.EMBEDDED:
        if strategy.artifact is not None:
            raise ValueError(f"embedded strategy for {slot.value} cannot declare an artifact")
        return
    if strategy.artifact is None or not strategy.artifact.is_file():
        raise ValueError(f"strategy artifact unavailable for {slot.value}: {strategy.artifact}")
    payload = yaml.safe_load(strategy.artifact.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"strategy artifact for {slot.value} must be a mapping")
    raw = cast("dict[str, object]", payload)
    artifact_version = raw.get("rule_version", raw.get("version"))
    if str(artifact_version) != strategy.version:
        raise ValueError(
            f"strategy version mismatch for {slot.value}: "
            f"definition={strategy.version}, artifact={artifact_version}"
        )


def _require_semver(value: str, label: str) -> None:
    if _SEMVER.fullmatch(value) is None:
        raise ValueError(f"{label} must be exact SemVer")


def _mapping(values: dict[str, object], key: str) -> dict[str, object]:
    value = values[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return cast("dict[str, object]", value)


def _decimal_value(values: dict[str, object], key: str) -> Decimal:
    try:
        value = Decimal(str(values[key]))
    except (KeyError, ValueError) as error:
        raise ValueError(f"strategy behavior {key} must be decimal") from error
    if not value.is_finite() or value < Decimal("0"):
        raise ValueError(f"strategy behavior {key} must be a non-negative finite decimal")
    return value


def _int_value(values: dict[str, object], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"strategy behavior {key} must be a positive integer")
    return value


def _bool_value(values: dict[str, object], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"strategy behavior {key} must be boolean")
    return value


def _with_confirmation_override(
    definition: MarketBotDefinition,
    version: str,
) -> MarketBotDefinition:
    """Translate the pre-assembly setting into one compatible implementation bundle."""

    implementations = {
        "2.0.0": {
            EngineSlot.SWING: "2.0.0",
            EngineSlot.INTRADAY: "2.0.0",
            EngineSlot.ENTRY_WATCHER: "2.0.0",
        },
        "3.0.0": {
            EngineSlot.SWING: "3.0.0",
            EngineSlot.INTRADAY: "3.0.0",
            EngineSlot.ENTRY_WATCHER: "3.0.0",
        },
        "4.0.0": {
            EngineSlot.SWING: "3.0.0",
            EngineSlot.INTRADAY: "4.0.0",
            EngineSlot.ENTRY_WATCHER: "4.0.0",
        },
        "5.0.0": {
            EngineSlot.SWING: "3.0.0",
            EngineSlot.INTRADAY: "4.0.0",
            EngineSlot.ENTRY_WATCHER: "5.0.0",
        },
    }[version]
    artifact = (
        definition.source.parent / f"../rules/entry_confirmation/{version}.yaml"
    ).resolve()
    engines = dict(definition.engines)
    for slot, implementation in implementations.items():
        spec = engines[slot]
        engines[slot] = replace(
            spec,
            implementation=implementation,
            strategy=EngineStrategy(
                kind=StrategyKind.ARTIFACT,
                version=version,
                artifact=artifact,
            ),
        )
    return replace(definition, engines=engines)
