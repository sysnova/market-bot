"""Injectable clocks for deterministic domain and infrastructure code."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of timezone-aware UTC instants."""

    def now(self) -> datetime:
        """Return the current instant in UTC."""
        ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock backed by the operating system."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class FrozenClock:
    """Mutable test clock whose progression is controlled by the caller."""

    _instant: datetime

    def __post_init__(self) -> None:
        if self._instant.tzinfo is None or self._instant.utcoffset() is None:
            msg = "FrozenClock requires a timezone-aware datetime"
            raise ValueError(msg)
        self._instant = self._instant.astimezone(UTC)

    def now(self) -> datetime:
        return self._instant

    def advance(self, delta: timedelta) -> datetime:
        """Advance the clock and return its new instant."""
        self._instant += delta
        return self._instant
