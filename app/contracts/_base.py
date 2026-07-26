"""Shared primitives for the version-one contracts."""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Sha256 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
SemVer = Annotated[
    str,
    StringConstraints(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$"),
]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"),
]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid7() -> UUID:
    """Generate an RFC 9562 UUIDv7 without requiring Python 3.14's uuid.uuid7."""

    timestamp_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    random_bits = secrets.randbits(74)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)


class StrictFrozenModel(BaseModel):
    """Base configuration shared by every public contract."""

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


UnitInterval = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"))]
PositiveDecimal = Annotated[Decimal, Field(gt=Decimal("0"))]
NonNegativeDecimal = Annotated[Decimal, Field(ge=Decimal("0"))]
