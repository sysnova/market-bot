"""Public API for the in-process rule registry."""

from .discovery import ENTRY_POINT_GROUP, discover_providers
from .errors import (
    DiscoveryError,
    DuplicateRuleError,
    EligibilityError,
    HashMismatchError,
    IncompatibleContractError,
    RegistryError,
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
from .registry import SUPPORTED_CONTRACT_VERSION, Registry

__all__ = [
    "ENTRY_POINT_GROUP",
    "SUPPORTED_CONTRACT_VERSION",
    "DiscoveryError",
    "DuplicateRuleError",
    "EligibilityError",
    "HashMismatchError",
    "IncompatibleContractError",
    "Registry",
    "RegistryError",
    "RegistryProvider",
    "RegistrySnapshot",
    "ResolvedRule",
    "RuleReference",
    "RuntimeEnvironment",
    "UnknownRuleError",
    "calculate_manifest_hash",
    "discover_providers",
]
