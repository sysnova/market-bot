"""Pure inputs and outputs owned by the leveraged-thesis engine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.contracts import (
    AnalysisHorizon,
    AnalysisResult,
    MarketSession,
    OrderFlowState,
    SupportAssessment,
)
from app.contracts.leveraged_thesis import (
    LeveragedExposure,
    LeveragedThesisAssessment,
    LeveragedThesisTransition,
)


def _empty_flows() -> Mapping[str, OrderFlowState]:
    return {}


@dataclass(frozen=True, slots=True)
class LeveragedPair:
    underlying_symbol: str
    bullish_instrument: str
    bearish_instrument: str
    bullish_exposure: LeveragedExposure = LeveragedExposure.LONG_2X
    bearish_exposure: LeveragedExposure = LeveragedExposure.INVERSE_2X

    def __post_init__(self) -> None:
        for name in ("underlying_symbol", "bullish_instrument", "bearish_instrument"):
            value = getattr(self, name).strip().upper()
            if not value or len(value) > 16:
                raise ValueError(f"invalid pair symbol: {name}")
            object.__setattr__(self, name, value)
        if self.bullish_exposure not in {
            LeveragedExposure.LONG_1X,
            LeveragedExposure.LONG_2X,
        }:
            raise ValueError("bullish exposure must be a long instrument")
        if self.bearish_exposure is not LeveragedExposure.INVERSE_2X:
            raise ValueError("bearish exposure must be an inverse instrument")


@dataclass(frozen=True, slots=True)
class LeveragedThesisContext:
    pair: LeveragedPair
    as_of: datetime
    session: MarketSession
    analysis: AnalysisResult | None
    underlying_flow: OrderFlowState
    support: SupportAssessment | None = None
    instrument_flows: Mapping[str, OrderFlowState] = field(default_factory=_empty_flows)
    previous_assessment: LeveragedThesisAssessment | None = None

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() != timedelta(0):
            raise ValueError("as_of must be timezone-aware UTC")
        if self.analysis is not None and (
            self.analysis.symbol != self.pair.underlying_symbol
            or self.analysis.horizon is not AnalysisHorizon.INTRADAY
        ):
            raise ValueError("analysis must be intraday evidence for the underlying")
        if self.underlying_flow.symbol != self.pair.underlying_symbol:
            raise ValueError("underlying order flow must match the pair")
        if self.support is not None and self.support.symbol != self.pair.underlying_symbol:
            raise ValueError("support assessment must match the pair underlying")
        allowed = {
            self.pair.bullish_instrument,
            self.pair.bearish_instrument,
        }
        if set(self.instrument_flows) - allowed or any(
            symbol != flow.symbol for symbol, flow in self.instrument_flows.items()
        ):
            raise ValueError("instrument flows must belong to pair instruments")
        if self.previous_assessment is not None and (
            self.previous_assessment.underlying_symbol != self.pair.underlying_symbol
        ):
            raise ValueError("previous assessment must belong to the same pair")


@dataclass(frozen=True, slots=True)
class LeveragedThesisEvaluation:
    assessment: LeveragedThesisAssessment
    transition: LeveragedThesisTransition | None
