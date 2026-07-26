"""Immutable registry values. This module contains no rule execution code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.common.canonical import sha256_digest
from app.contracts import RuleMetadata, RulePackManifest

_EXACT_REFERENCE = re.compile(
    r"^(?P<rule_id>[A-Za-z0-9][A-Za-z0-9._:/-]{0,127})@"
    r"(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)$"
)


class RuntimeEnvironment(StrEnum):
    """Execution environments relevant to lifecycle eligibility."""

    LIVE = "LIVE"
    PAPER = "PAPER"
    RESEARCH = "RESEARCH"


@dataclass(frozen=True, slots=True)
class RuleReference:
    rule_id: str
    version: str

    @classmethod
    def parse(cls, value: str) -> RuleReference:
        match = _EXACT_REFERENCE.fullmatch(value)
        if match is None:
            raise ValueError("rule reference must use exact rule_id@semver syntax")
        return cls(match.group("rule_id"), match.group("version"))

    def __str__(self) -> str:
        return f"{self.rule_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class RegistryProvider:
    """Trusted provider descriptor loaded explicitly or through an entry point."""

    provider_id: str
    contract_version: str
    manifest: RulePackManifest


@dataclass(frozen=True, slots=True)
class ResolvedRule:
    reference: RuleReference
    metadata: RuleMetadata
    provider_id: str
    manifest_hash: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """Content-addressed, immutable set of rules fixed for one run."""

    run_id: str
    rules: tuple[ResolvedRule, ...]
    snapshot_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "snapshot_hash",
            f"sha256:{sha256_digest(self.hash_payload())}",
        )

    def hash_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "rules": [
                {
                    "reference": str(rule.reference),
                    "implementation_hash": rule.metadata.implementation_hash,
                    "manifest_hash": rule.manifest_hash,
                }
                for rule in self.rules
            ],
        }
