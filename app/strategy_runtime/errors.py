"""Errors raised before a strategy can be executed."""


class StrategyRuntimeError(Exception):
    """Base error for the strategy runtime."""


class StrategyLoadError(StrategyRuntimeError, ValueError):
    """A strategy document is unsafe or invalid."""


class CompileError(StrategyRuntimeError, ValueError):
    """A strategy cannot be bound to an immutable registry snapshot."""
