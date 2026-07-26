from __future__ import annotations

from pathlib import Path

import pytest

from app.common.canonical import sha256_digest
from app.contracts import RuleLifecycleStatus, RulePackManifest
from app.rulepacks.synthetic_core.provider import ENTRY_POINT_GROUP, get_provider, get_providers


@pytest.mark.unit
def test_discovery_returns_one_provider_with_all_exact_coordinates() -> None:
    provider = get_provider()

    assert ENTRY_POINT_GROUP == "marketbot.rulepacks.v1"
    assert get_providers() == (provider,)
    assert provider.resolve("synthetic.threshold", "1.0.0").metadata.version == "1.0.0"
    assert provider.resolve("synthetic.threshold", "2.0.0").metadata.version == "2.0.0"
    coordinates = tuple(
        (rule.rule_id, rule.version) for rule in provider.manifest.rules
    )
    assert len(coordinates) == len(set(coordinates)) == 6

    manifest = provider.manifest
    manifest_data = manifest.model_dump(mode="python", exclude={"manifest_hash"})
    assert manifest.manifest_hash == f"sha256:{sha256_digest(manifest_data)}"
    assert RulePackManifest.model_validate(manifest) == manifest


@pytest.mark.unit
def test_synthetic_rules_are_paper_only_until_explicit_live_approval() -> None:
    provider = get_provider()

    assert {
        rule.lifecycle_status for rule in provider.manifest.rules
    } == {RuleLifecycleStatus.PAPER}


@pytest.mark.unit
def test_unknown_or_ambiguous_rules_are_rejected() -> None:
    provider = get_provider()

    with pytest.raises(KeyError, match="exact rule version"):
        provider.resolve("synthetic.threshold", "3.0.0")


@pytest.mark.unit
def test_provider_inventory_matches_golden() -> None:
    inventory = get_provider().inventory_json()
    assert inventory == (Path(__file__).parent / "goldens" / "inventory.json").read_bytes().strip()
