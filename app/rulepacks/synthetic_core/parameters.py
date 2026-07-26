"""Strict parameter models for the synthetic rules."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from app.contracts import Identifier, NonEmptyStr, StrictFrozenModel


class ReadNumberParameters(StrictFrozenModel):
    source: Identifier


class MultiplyParameters(StrictFrozenModel):
    value: Decimal
    factor: Decimal


class ThresholdV1Parameters(StrictFrozenModel):
    value: Decimal
    minimum: Decimal


class ThresholdV2Parameters(StrictFrozenModel):
    value: Decimal
    lower: Decimal
    upper: Decimal

    @model_validator(mode="after")
    def validate_bounds(self) -> ThresholdV2Parameters:
        if self.lower > self.upper:
            raise ValueError("lower must not exceed upper")
        return self


class ExceptionParameters(StrictFrozenModel):
    message: NonEmptyStr = "synthetic failure"


class TimeoutParameters(StrictFrozenModel):
    """Marker model for the deliberately non-terminating rule."""

    spin_marker: int = Field(default=1, ge=1, le=1)
