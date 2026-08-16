"""Strict internal schema returned by the news classifier."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator

from app.contracts._base import StrictFrozenModel


class NewsEventType(StrEnum):
    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    FDA = "FDA"
    CLINICAL_TRIAL = "CLINICAL_TRIAL"
    OFFERING = "OFFERING"
    DILUTION = "DILUTION"
    M_AND_A = "M_AND_A"
    CONTRACT = "CONTRACT"
    PRODUCT = "PRODUCT"
    MANAGEMENT = "MANAGEMENT"
    LEGAL = "LEGAL"
    REGULATORY = "REGULATORY"
    FRAUD = "FRAUD"
    ANALYST_RATING = "ANALYST_RATING"
    MACRO = "MACRO"
    RUMOR = "RUMOR"
    OTHER = "OTHER"


class NewsDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"


class NewsMateriality(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class NewsImpactHorizon(StrEnum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"
    LONG_TERM = "LONG_TERM"
    MULTI_HORIZON = "MULTI_HORIZON"


class NewsTickerAssessment(StrictFrozenModel):
    symbol: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.-]{0,15}$")
    relevant: bool
    event_type: NewsEventType
    direction: NewsDirection
    materiality: NewsMateriality
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    relevance: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    impact_horizon: NewsImpactHorizon
    expected_duration_hours: int = Field(ge=1, le=720)
    thesis: str = Field(min_length=1, max_length=500)
    evidence: tuple[str, ...] = Field(default=(), max_length=5)
    risk_flags: tuple[str, ...] = Field(default=(), max_length=10)
    insufficient_data: bool = False

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class NewsAssessmentBatch(StrictFrozenModel):
    article_id: int = Field(ge=1)
    assessments: tuple[NewsTickerAssessment, ...]
