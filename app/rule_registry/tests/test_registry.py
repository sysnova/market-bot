from datetime import UTC, datetime

import pytest

from app.common.canonical import sha256_digest
from app.contracts import (
    RuleLifecycleStatus,
    RuleMetadata,
    RulePackManifest,
    RuleType,
    StrategyMode,
)
from app.rule_registry import (
    DuplicateRuleError,
    EligibilityError,
    HashMismatchError,
    IncompatibleContractError,
    Registry,
    RegistryProvider,
    RuleReference,
    RuntimeEnvironment,
    calculate_manifest_hash,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def rule(
    rule_id: str = "threshold",
    version: str = "1.0.0",
    status: RuleLifecycleStatus = RuleLifecycleStatus.APPROVED,
    implementation_hash: str = "sha256:" + "a" * 64,
) -> RuleMetadata:
    return RuleMetadata(
        rule_id=rule_id,
        name=rule_id,
        version=version,
        rule_type=RuleType.FILTER,
        lifecycle_status=status,
        description="Synthetic test rule",
        implementation_hash=implementation_hash,
        created_at=NOW,
    )


def manifest(*rules: RuleMetadata) -> RulePackManifest:
    values = {
        "pack_id": "synthetic",
        "version": "1.0.0",
        "family": "synthetic",
        "engine": "reference",
        "rules": rules,
        "created_at": NOW,
        "description": "Synthetic pack",
    }
    return RulePackManifest(manifest_hash=calculate_manifest_hash(values), **values)


def provider(pack: RulePackManifest, contract_version: str = "1.0.0") -> RegistryProvider:
    return RegistryProvider(
        provider_id="tests.synthetic",
        contract_version=contract_version,
        manifest=pack,
    )


def test_registry_resolves_only_exact_references() -> None:
    registry = Registry()
    metadata = rule()
    registry.register(provider(manifest(metadata)))

    assert registry.resolve("threshold@1.0.0").metadata == metadata
    with pytest.raises(ValueError, match="rule_id@semver"):
        registry.resolve("threshold@1")
    with pytest.raises(ValueError, match="rule_id@semver"):
        RuleReference.parse("threshold")


def test_reference_accepts_the_full_contract_identifier_alphabet() -> None:
    assert RuleReference.parse("alpha.beta@1.2.3") == RuleReference("alpha.beta", "1.2.3")


def test_registry_rejects_invalid_manifest_hash() -> None:
    pack = manifest(rule()).model_copy(update={"manifest_hash": "sha256:" + "f" * 64})

    with pytest.raises(HashMismatchError):
        Registry().register(provider(pack))


def test_registry_rejects_duplicates_even_when_metadata_differs() -> None:
    registry = Registry()
    registry.register(provider(manifest(rule())))
    duplicate = rule(implementation_hash="sha256:" + "b" * 64)

    with pytest.raises(DuplicateRuleError, match=r"threshold@1\.0\.0"):
        registry.register(provider(manifest(duplicate)))


def test_registry_rejects_incompatible_contract_major() -> None:
    with pytest.raises(IncompatibleContractError):
        Registry().register(provider(manifest(rule()), contract_version="2.0.0"))


@pytest.mark.parametrize(
    ("status", "mode", "environment", "eligible"),
    [
        (RuleLifecycleStatus.APPROVED, StrategyMode.PRIMARY, RuntimeEnvironment.LIVE, True),
        (RuleLifecycleStatus.PAPER, StrategyMode.PRIMARY, RuntimeEnvironment.LIVE, False),
        (RuleLifecycleStatus.PAPER, StrategyMode.PRIMARY, RuntimeEnvironment.PAPER, True),
        (RuleLifecycleStatus.VALIDATED, StrategyMode.SHADOW, RuntimeEnvironment.LIVE, True),
        (RuleLifecycleStatus.DRAFT, StrategyMode.SHADOW, RuntimeEnvironment.LIVE, False),
        (RuleLifecycleStatus.DRAFT, StrategyMode.RESEARCH, RuntimeEnvironment.RESEARCH, True),
        (RuleLifecycleStatus.DEPRECATED, StrategyMode.RESEARCH, RuntimeEnvironment.RESEARCH, False),
    ],
)
def test_eligibility_matrix(
    status: RuleLifecycleStatus,
    mode: StrategyMode,
    environment: RuntimeEnvironment,
    eligible: bool,
) -> None:
    registry = Registry()
    registry.register(provider(manifest(rule(status=status))))

    if eligible:
        resolved = registry.resolve_eligible("threshold@1.0.0", mode, environment)
        assert resolved.metadata.lifecycle_status is status
    else:
        with pytest.raises(EligibilityError):
            registry.resolve_eligible("threshold@1.0.0", mode, environment)


def test_research_draft_adds_warning_and_deprecated_needs_explicit_replay() -> None:
    registry = Registry()
    registry.register(provider(manifest(rule(status=RuleLifecycleStatus.DRAFT))))
    draft = registry.resolve_eligible(
        "threshold@1.0.0", StrategyMode.RESEARCH, RuntimeEnvironment.RESEARCH
    )
    assert draft.warnings == ("DRAFT rule enabled for RESEARCH only",)

    deprecated_registry = Registry()
    deprecated_registry.register(
        provider(manifest(rule(status=RuleLifecycleStatus.DEPRECATED)))
    )
    replayed = deprecated_registry.resolve_eligible(
        "threshold@1.0.0",
        StrategyMode.RESEARCH,
        RuntimeEnvironment.RESEARCH,
        allow_deprecated_replay=True,
    )
    assert replayed.warnings == ("DEPRECATED rule enabled for explicit replay",)


def test_snapshot_is_immutable_and_content_addressed() -> None:
    registry = Registry()
    registry.register(provider(manifest(rule())))

    snapshot = registry.snapshot(
        run_id="run-001",
        references=("threshold@1.0.0",),
        mode=StrategyMode.PRIMARY,
        environment=RuntimeEnvironment.LIVE,
    )

    assert snapshot.run_id == "run-001"
    assert snapshot.rules[0].reference == RuleReference("threshold", "1.0.0")
    assert snapshot.snapshot_hash == f"sha256:{sha256_digest(snapshot.hash_payload())}"
    with pytest.raises(AttributeError):
        snapshot.run_id = "other"  # type: ignore[misc]
