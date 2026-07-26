"""Sortable UUIDv7 and human-readable typed identifier helpers."""

import re
import secrets
from collections.abc import Callable
from uuid import UUID

from app.common.clock import Clock, SystemClock

_PREFIX = re.compile(r"^[a-z][a-z0-9]{1,31}$")


def new_uuid7(
    *,
    clock: Clock | None = None,
    randbits: Callable[[int], int] = secrets.randbits,
) -> UUID:
    """Create an RFC 9562 UUIDv7, with injectable time and randomness for tests."""
    timestamp_ms = int((clock or SystemClock()).now().timestamp() * 1_000)
    if not 0 <= timestamp_ms < 1 << 48:
        msg = "UUIDv7 timestamp must fit in 48 bits"
        raise ValueError(msg)

    random_bits = randbits(74)
    if not 0 <= random_bits < 1 << 74:
        msg = "randbits(74) returned a value outside the 74-bit range"
        raise ValueError(msg)

    random_a = random_bits >> 62
    random_b = random_bits & ((1 << 62) - 1)
    value = (
        (timestamp_ms << 80)
        | (0b0111 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return UUID(int=value)


def new_id(
    prefix: str,
    *,
    clock: Clock | None = None,
    randbits: Callable[[int], int] = secrets.randbits,
) -> str:
    """Return ``<prefix>_<uuid7>`` after validating a storage-safe prefix."""
    if _PREFIX.fullmatch(prefix) is None:
        msg = "ID prefix must be 2-32 lowercase alphanumeric characters, starting with a letter"
        raise ValueError(msg)
    return f"{prefix}_{new_uuid7(clock=clock, randbits=randbits)}"
