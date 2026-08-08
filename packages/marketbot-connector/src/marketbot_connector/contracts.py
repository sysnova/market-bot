"""Minimal public wire contracts required by the standalone connector."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"),
]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SemVer = Annotated[
    str,
    StringConstraints(
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$"
    ),
]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid7() -> UUID:
    """Generate an RFC 9562 UUIDv7 on every supported Python version."""

    timestamp_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
        use_enum_values=False,
    )

    @field_validator("*", mode="after")
    @classmethod
    def validate_utc_datetimes(cls, value: object) -> object:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() != timedelta(0)
        ):
            raise ValueError("timestamps must be timezone-aware UTC values")
        return value


class MarketSession(StrEnum):
    PRE_MARKET = "PRE_MARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"
    CONTINUOUS = "CONTINUOUS"


class NamedValue(StrictFrozenModel):
    name: Identifier
    value: Any


class EventEnvelope(StrictFrozenModel):
    """Stable MarketBot envelope; payload remains event-specific JSON."""

    event_id: UUID = Field(default_factory=new_uuid7)
    event_type: Identifier
    schema_version: SemVer = "1.0.0"
    occurred_at: datetime = Field(default_factory=utc_now)
    source: Identifier
    trace_id: UUID | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    market_session: MarketSession | None = None
    subject: NonEmptyStr | None = None
    payload: Any | None = None
    attributes: tuple[NamedValue, ...] = ()

    @field_validator("event_id")
    @classmethod
    def require_uuid7(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise ValueError("event_id must be a UUIDv7")
        return value


def encode_envelope(envelope: EventEnvelope) -> bytes:
    return envelope.model_dump_json().encode("utf-8")


def decode_envelope(payload: bytes) -> EventEnvelope:
    return EventEnvelope.model_validate_json(payload, strict=True)
