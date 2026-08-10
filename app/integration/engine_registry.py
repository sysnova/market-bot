"""Generic registry for versioned engine implementations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app.common.strategy import StrategySource

from .marketbot_definition import EngineSlot, EngineSpec

EngineFactory = Callable[..., object]
EngineConfigurator = Callable[
    [str, StrategySource, tuple[object, ...], dict[str, object]],
    tuple[tuple[object, ...], dict[str, object]],
]
StrategyValidator = Callable[[str, StrategySource], None]
StrategyResolver = Callable[[str, StrategySource, dict[str, object]], object]


def _identity_configuration(
    implementation: str,
    source: StrategySource,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[tuple[object, ...], dict[str, object]]:
    del implementation, source
    return args, kwargs


def _accept_strategy(implementation: str, source: StrategySource) -> None:
    del implementation, source


@dataclass(frozen=True, slots=True)
class EngineRegistration:
    """All integration metadata for one logical engine slot."""

    implementations: Mapping[str, EngineFactory]
    required_since: str
    configure: EngineConfigurator = _identity_configuration
    validate_strategy: StrategyValidator = _accept_strategy
    strategy_resolver: StrategyResolver | None = None

    @classmethod
    def simple(
        cls,
        *,
        implementations: Mapping[str, EngineFactory],
        required_since: str = "0.0.0",
    ) -> EngineRegistration:
        return cls(
            implementations=MappingProxyType(dict(implementations)),
            required_since=required_since,
        )

    def validate(self, spec: EngineSpec) -> None:
        if spec.implementation not in self.implementations:
            raise ValueError(f"unregistered implementation: {spec.implementation}")
        self.validate_strategy(
            spec.implementation,
            StrategySource(version=spec.strategy.version, artifact=spec.strategy.artifact),
        )

    def build(
        self,
        spec: EngineSpec,
        *args: object,
        strategy_artifact_override: Path | None = None,
        **kwargs: object,
    ) -> object:
        source = _strategy_source(spec, strategy_artifact_override)
        if strategy_artifact_override is not None:
            self.validate_strategy(spec.implementation, source)
        resolved_args, resolved_kwargs = self.configure(
            spec.implementation,
            source,
            args,
            dict(kwargs),
        )
        return self.implementations[spec.implementation](
            *resolved_args,
            **resolved_kwargs,
        )

    def resolve_strategy(
        self,
        spec: EngineSpec,
        *,
        artifact_override: Path | None = None,
        **context: object,
    ) -> object:
        """Resolve engine-owned runtime policy without exposing artifact parsing."""

        if self.strategy_resolver is None:
            raise ValueError("engine registration does not expose a runtime strategy")
        source = _strategy_source(spec, artifact_override)
        self.validate_strategy(spec.implementation, source)
        return self.strategy_resolver(spec.implementation, source, dict(context))


class EngineRegistry:
    """Mutable only during composition; duplicate ownership is rejected."""

    def __init__(
        self,
        registrations: Mapping[EngineSlot, EngineRegistration] | None = None,
    ) -> None:
        self._registrations = dict(registrations or {})

    def register(self, slot: EngineSlot, registration: EngineRegistration) -> None:
        if slot in self._registrations:
            raise ValueError(f"engine slot already registered: {slot.value}")
        self._registrations[slot] = registration

    def registration(self, slot: EngineSlot) -> EngineRegistration:
        try:
            return self._registrations[slot]
        except KeyError as error:
            raise ValueError(f"engine slot is not registered: {slot.value}") from error

    def required_slots(self, definition_version: str) -> frozenset[EngineSlot]:
        current = _semver_tuple(definition_version)
        return frozenset(
            slot
            for slot, registration in self._registrations.items()
            if _semver_tuple(registration.required_since) <= current
        )

    def slots(self) -> frozenset[EngineSlot]:
        return frozenset(self._registrations)


def _semver_tuple(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"registration version must be exact SemVer: {value}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def _strategy_source(
    spec: EngineSpec,
    artifact_override: Path | None,
) -> StrategySource:
    return StrategySource(
        version=spec.strategy.version,
        artifact=artifact_override or spec.strategy.artifact,
    )
