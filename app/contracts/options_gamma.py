"""Stable options-gamma context shared without coupling consumer engines."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from ._base import (
    Identifier,
    NonEmptyStr,
    NonNegativeDecimal,
    PositiveDecimal,
    SemVer,
    Sha256,
    StrictFrozenModel,
    UnitInterval,
    new_uuid7,
)

GammaStatus = Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE"]
GammaRegime = Literal["POSITIVE", "MIXED", "NEGATIVE", "UNKNOWN"]
GammaDirectionalBias = Literal["UP", "DOWN", "NEUTRAL", "UNRELIABLE"]


class GammaExpirationAssessment(StrictFrozenModel):
    """Aggregated options positioning for one expiration date."""

    expiration_date: date
    days_to_expiration: int = Field(ge=0)
    contract_count: int = Field(ge=0)
    usable_contract_count: int = Field(ge=0)
    open_interest: NonNegativeDecimal
    net_gamma_exposure: Decimal
    absolute_gamma_exposure: NonNegativeDecimal
    call_wall: PositiveDecimal | None = None
    put_wall: PositiveDecimal | None = None
    absolute_gamma_wall: PositiveDecimal | None = None
    max_pain: PositiveDecimal | None = None
    gamma_flip: PositiveDecimal | None = None
    expected_move_low: PositiveDecimal | None = None
    expected_move_high: PositiveDecimal | None = None
    influence_weight: UnitInterval

    @model_validator(mode="after")
    def validate_expiration(self) -> GammaExpirationAssessment:
        if self.usable_contract_count > self.contract_count:
            raise ValueError("usable contract count cannot exceed contract count")
        if (
            self.expected_move_low is not None
            and self.expected_move_high is not None
            and self.expected_move_low >= self.expected_move_high
        ):
            raise ValueError("expected move low must be below expected move high")
        return self


class GammaAssessment(StrictFrozenModel):
    """Latest bounded options-gamma context for one underlying symbol."""

    assessment_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    generated_at: datetime
    expires_at: datetime
    engine_version: SemVer
    methodology_version: SemVer
    spot_price: PositiveDecimal
    spot_as_of: datetime
    expiration_from: date
    expiration_to: date
    open_interest_as_of: date | None = None
    status: GammaStatus
    quality_score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    contract_count: int = Field(ge=0)
    usable_contract_count: int = Field(ge=0)
    coverage_ratio: UnitInterval
    gamma_regime: GammaRegime
    directional_bias: GammaDirectionalBias
    net_gamma_exposure: Decimal
    absolute_gamma_exposure: NonNegativeDecimal
    net_gamma_ratio: Decimal | None = Field(default=None, ge=Decimal("-1"), le=Decimal("1"))
    call_wall: PositiveDecimal | None = None
    put_wall: PositiveDecimal | None = None
    absolute_gamma_wall: PositiveDecimal | None = None
    max_pain: PositiveDecimal | None = None
    gamma_flip: PositiveDecimal | None = None
    expected_move_low: PositiveDecimal | None = None
    expected_move_high: PositiveDecimal | None = None
    pin_risk: bool = False
    acceleration_risk: bool = False
    dealer_sign_assumption: Literal["CALL_POSITIVE_PUT_NEGATIVE"]
    expirations: tuple[GammaExpirationAssessment, ...] = ()
    warnings: tuple[NonEmptyStr, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_assessment(self) -> GammaAssessment:
        if self.assessment_id.version != 7:
            raise ValueError("assessment_id must be UUIDv7")
        if self.expires_at <= self.generated_at:
            raise ValueError("expires_at must be later than generated_at")
        if self.expiration_to < self.expiration_from:
            raise ValueError("expiration_to cannot precede expiration_from")
        if self.usable_contract_count > self.contract_count:
            raise ValueError("usable contract count cannot exceed contract count")
        expected_coverage = (
            Decimal()
            if self.contract_count == 0
            else Decimal(self.usable_contract_count) / Decimal(self.contract_count)
        )
        if abs(self.coverage_ratio - expected_coverage) > Decimal("0.0001"):
            raise ValueError("coverage_ratio must match usable contract coverage")
        if self.status == "UNAVAILABLE" and any(
            value is not None
            for value in (
                self.call_wall,
                self.put_wall,
                self.absolute_gamma_wall,
                self.max_pain,
                self.gamma_flip,
                self.expected_move_low,
                self.expected_move_high,
            )
        ):
            raise ValueError("UNAVAILABLE gamma cannot publish analytical levels")
        if (
            self.expected_move_low is not None
            and self.expected_move_high is not None
            and self.expected_move_low >= self.expected_move_high
        ):
            raise ValueError("expected move low must be below expected move high")
        dates = tuple(item.expiration_date for item in self.expirations)
        if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
            raise ValueError("gamma expirations must be unique and ordered")
        return self
