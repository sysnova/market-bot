"""Additive, observation-only diagnostics and independently timestamped quotes."""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from ._base import Identifier, NonEmptyStr, SemVer, StrictFrozenModel
from .microstructure_events import _token  # pyright: ignore[reportPrivateUsage]
from .order_flow import MarketQuote
from .rules import NamedValue

EXECUTION_QUOTE_EVENT = "execution-quote.observed"
SHORT_OBSERVATION_EVENT = "leveraged-thesis.short-observed"


class ExecutionQuoteSnapshot(StrictFrozenModel):
    quote: MarketQuote
    published_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.published_at < self.quote.received_at:
            raise ValueError("publication cannot precede quote receipt")
        return self


class ShortGate(StrictFrozenModel):
    name: Identifier
    passed: bool
    reason: NonEmptyStr


class ShortObservation(StrictFrozenModel):
    symbol: Identifier
    instrument_symbol: Identifier
    occurred_at: datetime
    engine_version: SemVer
    route: Literal["SHORT_DAILY", "SHORT_TACTICAL"]
    mode: Literal["OBSERVE"] = "OBSERVE"
    orders_enabled: Literal[False] = False
    status: Literal["BLOCKED", "READY", "INVALIDATED", "EXPIRED", "OBJECTIVE_REACHED"]
    setup_id: NonEmptyStr | None = None
    gates: tuple[ShortGate, ...] = Field(min_length=1)
    remaining_reward_risk: Decimal | None = None
    instrument_reward_risk: Decimal | None = None
    metrics: tuple[NamedValue, ...] = ()

    @model_validator(mode="after")
    def validate_ready(self) -> Self:
        if len({gate.name for gate in self.gates}) != len(self.gates):
            raise ValueError("gate names must be unique")
        if self.status == "READY" and not all(g.passed for g in self.gates):
            raise ValueError("READY requires all gates to pass")
        return self


def execution_quote_subject(symbol: str) -> str:
    return f"marketbot.v1.execution-quote.{_token(symbol)}"


def short_observation_subject(route: str, symbol: str) -> str:
    if route not in {"SHORT_DAILY", "SHORT_TACTICAL"}:
        raise ValueError("unknown SHORT observation route")
    return f"marketbot.v1.leveraged-thesis.short-observation.{route}.{_token(symbol)}"
