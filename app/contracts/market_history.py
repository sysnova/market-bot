"""Versioned request/reply contracts for centralized market history."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import cast
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ._base import Identifier, NonEmptyStr, StrictFrozenModel, new_uuid7
from .enums import BarTimeframe

MARKET_HISTORY_ENSURE_SUBJECT = "marketbot.rpc.v1.market.history.ensure"


class MarketHistoryStatus(StrEnum):
    READY = "READY"
    ERROR = "ERROR"


class MarketHistoryRequirement(StrictFrozenModel):
    timeframe: BarTimeframe
    lookback: timedelta = Field(gt=timedelta(0))
    max_bars_per_symbol: int = Field(ge=1, le=10_000)


class MarketHistoryRequest(StrictFrozenModel):
    request_id: UUID = Field(default_factory=new_uuid7)
    engine_id: Identifier
    symbols: tuple[Identifier, ...] = Field(min_length=1)
    requirements: tuple[MarketHistoryRequirement, ...] = Field(min_length=1)
    requested_at: datetime

    @field_validator("symbols", mode="before")
    @classmethod
    def normalize_symbols(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            return value
        symbols = cast("tuple[object, ...] | list[object]", value)
        return tuple(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols))

    @model_validator(mode="after")
    def validate_request(self) -> MarketHistoryRequest:
        if self.request_id.version != 7:
            raise ValueError("request_id must be a UUIDv7")
        timeframes = tuple(item.timeframe for item in self.requirements)
        if len(timeframes) != len(set(timeframes)):
            raise ValueError("requirements must contain unique timeframes")
        return self


class MarketHistoryResponse(StrictFrozenModel):
    request_id: UUID = Field(default_factory=new_uuid7)
    status: MarketHistoryStatus
    synced_through: datetime
    persisted_bars: int = Field(ge=0)
    error: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_status(self) -> MarketHistoryResponse:
        if self.status is MarketHistoryStatus.ERROR and self.error is None:
            raise ValueError("error response requires an error message")
        if self.status is MarketHistoryStatus.READY and self.error is not None:
            raise ValueError("ready response cannot contain an error")
        return self
