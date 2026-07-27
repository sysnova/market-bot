"""Stable market-data, analytical result, and local-alert contracts."""

from datetime import datetime
from decimal import Decimal
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
from .enums import (
    AlertSeverity,
    AnalysisHorizon,
    AnalysisVerdict,
    BarTimeframe,
    PatternDirection,
)
from .rules import NamedValue

AnalysisScore = Decimal


class MarketBar(StrictFrozenModel):
    """A completed or updating OHLCV bar from a market-data source."""

    symbol: Identifier
    timeframe: BarTimeframe
    timestamp: datetime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal
    trade_count: int | None = Field(default=None, ge=0)
    vwap: PositiveDecimal | None = None
    source: Identifier
    feed: Identifier
    is_final: bool = True

    @model_validator(mode="after")
    def validate_ohlc(self) -> MarketBar:
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must be greater than or equal to all OHLC prices")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must be less than or equal to all OHLC prices")
        return self


class AnalysisResult(StrictFrozenModel):
    """Deterministic output emitted by one analysis engine."""

    analysis_id: UUID = Field(default_factory=new_uuid7)
    engine_id: Identifier
    engine_version: SemVer
    symbol: Identifier
    horizon: AnalysisHorizon
    as_of: datetime
    verdict: AnalysisVerdict
    direction: PatternDirection
    score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    confidence: UnitInterval
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    metrics: tuple[NamedValue, ...] = ()
    source_event_ids: tuple[UUID, ...] = ()
    context_hash: Sha256

    @model_validator(mode="after")
    def validate_identity_and_collections(self) -> AnalysisResult:
        if self.analysis_id.version != 7:
            raise ValueError("analysis_id must be a UUIDv7")
        metric_names = tuple(metric.name for metric in self.metrics)
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("metrics must be unique by name")
        if len(self.source_event_ids) != len(set(self.source_event_ids)):
            raise ValueError("source_event_ids must be unique")
        return self


class LocalAlert(StrictFrozenModel):
    """An analytical notification for a human; it cannot express an order."""

    alert_id: UUID = Field(default_factory=new_uuid7)
    symbol: Identifier
    created_at: datetime
    severity: AlertSeverity
    title: NonEmptyStr
    message: NonEmptyStr
    horizons: tuple[AnalysisHorizon, ...] = Field(min_length=1)
    component_analysis_ids: tuple[UUID, ...] = Field(min_length=1)
    component_analyses: tuple[AnalysisResult, ...] = ()
    metrics: tuple[NamedValue, ...] = ()
    score: Decimal = Field(ge=Decimal("0"), le=Decimal("100"))
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    deduplication_key: NonEmptyStr
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_identity_and_links(self) -> LocalAlert:
        if self.alert_id.version != 7:
            raise ValueError("alert_id must be a UUIDv7")
        if len(self.horizons) != len(set(self.horizons)):
            raise ValueError("horizons must be unique")
        if len(self.component_analysis_ids) != len(set(self.component_analysis_ids)):
            raise ValueError("component_analysis_ids must be unique")
        embedded_ids = tuple(item.analysis_id for item in self.component_analyses)
        if len(embedded_ids) != len(set(embedded_ids)):
            raise ValueError("component_analyses must be unique")
        if any(item.symbol != self.symbol for item in self.component_analyses):
            raise ValueError("component analyses must belong to the alert symbol")
        metric_names = tuple(item.name for item in self.metrics)
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("alert metrics must be unique by name")
        if self.expires_at is not None and self.expires_at < self.created_at:
            raise ValueError("expires_at must not be before created_at")
        return self
