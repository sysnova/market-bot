"""In-process registry of trusted rule metadata."""

from __future__ import annotations

from app.contracts import RuleLifecycleStatus, StrategyMode

from .errors import (
    DuplicateRuleError,
    EligibilityError,
    HashMismatchError,
    IncompatibleContractError,
    UnknownRuleError,
)
from .hashing import calculate_manifest_hash
from .models import (
    RegistryProvider,
    RegistrySnapshot,
    ResolvedRule,
    RuleReference,
    RuntimeEnvironment,
)

SUPPORTED_CONTRACT_VERSION = "1"
_COMPATIBLE_CONTRACT_VERSIONS = frozenset({"1", "1.0.0"})


class Registry:
    """Indexes metadata; it deliberately has no API for executing rule code."""

    def __init__(self) -> None:
        self._rules: dict[RuleReference, ResolvedRule] = {}

    def register(self, provider: RegistryProvider) -> None:
        if provider.contract_version not in _COMPATIBLE_CONTRACT_VERSIONS:
            raise IncompatibleContractError(
                f"provider {provider.provider_id!r} targets contracts "
                f"{provider.contract_version}; expected {SUPPORTED_CONTRACT_VERSION}"
            )
        expected_hash = calculate_manifest_hash(provider.manifest)
        if provider.manifest.manifest_hash != expected_hash:
            raise HashMismatchError(
                f"manifest {provider.manifest.pack_id!r} digest does not match its contents"
            )

        pending: list[tuple[RuleReference, ResolvedRule]] = []
        for metadata in provider.manifest.rules:
            reference = RuleReference(metadata.rule_id, metadata.version)
            if reference in self._rules or any(item[0] == reference for item in pending):
                raise DuplicateRuleError(f"duplicate rule reference {reference}")
            pending.append(
                (
                    reference,
                    ResolvedRule(
                        reference=reference,
                        metadata=metadata,
                        provider_id=provider.provider_id,
                        manifest_hash=provider.manifest.manifest_hash,
                    ),
                )
            )
        self._rules.update(pending)

    def resolve(self, reference: str | RuleReference) -> ResolvedRule:
        parsed = RuleReference.parse(reference) if isinstance(reference, str) else reference
        try:
            return self._rules[parsed]
        except KeyError as error:
            raise UnknownRuleError(f"unknown exact rule reference {parsed}") from error

    def resolve_eligible(
        self,
        reference: str | RuleReference,
        mode: StrategyMode,
        environment: RuntimeEnvironment,
        *,
        allow_deprecated_replay: bool = False,
    ) -> ResolvedRule:
        resolved = self.resolve(reference)
        status = resolved.metadata.lifecycle_status
        warning = self._eligibility_warning(
            status, mode, environment, allow_deprecated_replay=allow_deprecated_replay
        )
        if warning is None:
            return resolved
        return ResolvedRule(
            reference=resolved.reference,
            metadata=resolved.metadata,
            provider_id=resolved.provider_id,
            manifest_hash=resolved.manifest_hash,
            warnings=(warning,),
        )

    def snapshot(
        self,
        run_id: str,
        references: tuple[str | RuleReference, ...],
        mode: StrategyMode,
        environment: RuntimeEnvironment,
        *,
        allow_deprecated_replay: bool = False,
    ) -> RegistrySnapshot:
        rules = tuple(
            self.resolve_eligible(
                reference,
                mode,
                environment,
                allow_deprecated_replay=allow_deprecated_replay,
            )
            for reference in references
        )
        return RegistrySnapshot(run_id=run_id, rules=rules)

    @staticmethod
    def _eligibility_warning(
        status: RuleLifecycleStatus,
        mode: StrategyMode,
        environment: RuntimeEnvironment,
        *,
        allow_deprecated_replay: bool,
    ) -> str | None:
        if status is RuleLifecycleStatus.DEPRECATED:
            if allow_deprecated_replay:
                return "DEPRECATED rule enabled for explicit replay"
            raise EligibilityError("DEPRECATED rules require explicit replay")
        if mode is StrategyMode.DISABLED:
            raise EligibilityError("DISABLED strategies cannot resolve eligible rules")
        if mode is StrategyMode.PRIMARY:
            allowed = (
                status is RuleLifecycleStatus.APPROVED
                if environment is RuntimeEnvironment.LIVE
                else environment is RuntimeEnvironment.PAPER
                and status in {RuleLifecycleStatus.PAPER, RuleLifecycleStatus.APPROVED}
            )
        elif mode is StrategyMode.SHADOW:
            allowed = status in {
                RuleLifecycleStatus.VALIDATED,
                RuleLifecycleStatus.PAPER,
                RuleLifecycleStatus.APPROVED,
            }
        else:
            allowed = status in {
                RuleLifecycleStatus.DRAFT,
                RuleLifecycleStatus.VALIDATED,
                RuleLifecycleStatus.PAPER,
                RuleLifecycleStatus.APPROVED,
            }
        if not allowed:
            raise EligibilityError(
                f"{status} is not eligible for {mode} in {environment}"
            )
        if mode is StrategyMode.RESEARCH and status is RuleLifecycleStatus.DRAFT:
            return "DRAFT rule enabled for RESEARCH only"
        return None
