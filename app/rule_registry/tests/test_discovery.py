from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import EntryPoint

import pytest

from app.contracts import RulePackManifest
from app.rule_registry import (
    ENTRY_POINT_GROUP,
    DiscoveryError,
    RegistryProvider,
    discover_providers,
)


class FakeEntryPoint:
    def __init__(self, name: str, group: str, loaded: object) -> None:
        self.name = name
        self.group = group
        self.value = "tests:provider"
        self._loaded = loaded

    def load(self) -> object:
        return self._loaded


def test_discovery_loads_only_the_versioned_entry_point_group() -> None:
    calls: list[str] = []

    def select(*, group: str) -> tuple[EntryPoint, ...]:
        calls.append(group)
        return ()

    assert discover_providers(select=select) == ()
    assert calls == [ENTRY_POINT_GROUP]


def test_discovery_rejects_an_object_that_is_not_a_provider() -> None:
    fake = FakeEntryPoint("invalid", ENTRY_POINT_GROUP, object())

    with pytest.raises(DiscoveryError, match="invalid"):
        discover_providers(select=lambda **_: (fake,))  # type: ignore[arg-type]


def test_discovery_accepts_a_provider_factory_returning_a_tuple() -> None:
    fake = FakeEntryPoint("synthetic", ENTRY_POINT_GROUP, lambda: ())

    assert discover_providers(select=lambda **_: (fake,)) == ()  # type: ignore[arg-type]


def test_discovery_converts_structural_provider_without_retaining_rules() -> None:
    pack = RulePackManifest(
        pack_id="structural_pack",
        version="1.2.3",
        family="synthetic",
        engine="reference",
        manifest_hash="sha256:" + "a" * 64,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    @dataclass(frozen=True)
    class StructuralProvider:
        manifest: RulePackManifest
        rules: tuple[object, ...]

    def executable() -> None:
        return None

    fake = FakeEntryPoint(
        "structural",
        ENTRY_POINT_GROUP,
        lambda: (StructuralProvider(pack, (executable,)),),
    )

    discovered = discover_providers(select=lambda **_: (fake,))  # type: ignore[arg-type]

    assert discovered == (
        RegistryProvider(
            provider_id="structural_pack@1.2.3",
            contract_version="1",
            manifest=pack,
        ),
    )
    assert all(not hasattr(provider, "rules") for provider in discovered)
