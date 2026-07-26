"""Trusted package discovery through one versioned entry point group."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from typing import Protocol, cast

from app.contracts import RulePackManifest

from .errors import DiscoveryError
from .models import RegistryProvider

ENTRY_POINT_GROUP = "marketbot.rulepacks.v1"


class EntryPointSelector(Protocol):
    def __call__(self, *, group: str) -> Iterable[EntryPoint]: ...


def _default_select(*, group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


def _as_registry_provider(candidate: object) -> RegistryProvider | None:
    if isinstance(candidate, RegistryProvider):
        return candidate
    manifest = getattr(candidate, "manifest", None)
    if not isinstance(manifest, RulePackManifest):
        return None
    return RegistryProvider(
        provider_id=f"{manifest.pack_id}@{manifest.version}",
        contract_version="1",
        manifest=manifest,
    )


def discover_providers(
    *, select: EntryPointSelector = _default_select
) -> tuple[RegistryProvider, ...]:
    """Load descriptors only from the allowlisted entry-point group."""

    providers: list[RegistryProvider] = []
    for entry_point in select(group=ENTRY_POINT_GROUP):
        loaded = cast(object, entry_point.load())
        candidate: object = (
            cast(Callable[[], object], loaded)() if callable(loaded) else loaded
        )
        candidates: tuple[object, ...] = (
            cast(tuple[object, ...], candidate)
            if isinstance(candidate, tuple)
            else (candidate,)
        )
        descriptors = tuple(_as_registry_provider(item) for item in candidates)
        if any(item is None for item in descriptors):
            raise DiscoveryError(
                f"entry point {entry_point.name!r} did not expose RegistryProvider values"
            )
        providers.extend(cast(tuple[RegistryProvider, ...], descriptors))
    return tuple(providers)
