"""Rule registry errors."""


class RegistryError(Exception):
    """Base class for deterministic registry failures."""


class DuplicateRuleError(RegistryError):
    """An exact rule reference is already registered."""


class HashMismatchError(RegistryError):
    """A supplied manifest digest does not match its semantic contents."""


class IncompatibleContractError(RegistryError):
    """A provider targets an unsupported contracts version."""


class UnknownRuleError(RegistryError):
    """An exact rule reference is not registered."""


class EligibilityError(RegistryError):
    """A rule lifecycle is not eligible for the requested execution mode."""


class DiscoveryError(RegistryError):
    """An entry point does not expose a valid provider descriptor."""

