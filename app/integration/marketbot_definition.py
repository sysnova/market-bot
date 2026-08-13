"""Lightweight MarketBot definition model and YAML loader."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml

from app.common.settings import AppSettings

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class EngineSlot(StrEnum):
    LONG_TERM = "long-term"
    SWING = "swing"
    INTRADAY = "intraday"
    ENTRY_WATCHER = "entry-watcher"
    ENTRY_OPPORTUNITY = "entry-opportunity"
    ENTRY_RECOVERY = "entry-recovery"
    ALERT = "alert"
    MARKET_ROTATION = "market-rotation"
    PORTFOLIO_FLOW = "portfolio-flow"
    LONG_PORTFOLIO = "long-portfolio"
    PATREON_CAPS = "patreon-caps"
    ELLIOTT_WAVE = "elliott-wave"
    SUPPORT_CONFIRMATION = "support-confirmation"
    VOLUME_STRUCTURE = "volume-structure"
    OPTIONS_GAMMA = "options-gamma"
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


def load_marketbot_definition(path: Path) -> MarketBotDefinition:
    source = path.resolve()
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("MarketBot definition must be a mapping")
    raw = cast("dict[str, object]", payload)
    raw_engines = require_mapping(raw, "engines")
    engines: dict[EngineSlot, EngineSpec] = {}
    for raw_slot, raw_spec in raw_engines.items():
        slot = EngineSlot(raw_slot)
        if not isinstance(raw_spec, dict):
            raise ValueError(f"engine {slot.value} must be a mapping")
        spec = cast("dict[str, object]", raw_spec)
        raw_strategy = require_mapping(spec, "strategy")
        artifact_value = raw_strategy.get("artifact")
        artifact = (
            (source.parent / str(artifact_value)).resolve()
            if artifact_value is not None
            else None
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


def load_configured_marketbot_definition(settings: AppSettings) -> MarketBotDefinition:
    """Load the selected definition and translate the deprecated rollback override."""

    definition = load_marketbot_definition(settings.definition_path)
    override = settings.entry_confirmation_rule_version
    if override is not None:
        definition = with_confirmation_override(definition, override)
    return definition


def validate_engine_strategy(slot: EngineSlot, strategy: EngineStrategy) -> None:
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


def require_exact_semver(value: str, label: str) -> None:
    if _SEMVER.fullmatch(value) is None:
        raise ValueError(f"{label} must be exact SemVer")


def require_mapping(values: dict[str, object], key: str) -> dict[str, object]:
    value = values[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return cast("dict[str, object]", value)


def with_confirmation_override(
    definition: MarketBotDefinition,
    version: str,
) -> MarketBotDefinition:
    """Translate the deprecated setting into one compatible implementation bundle."""

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
