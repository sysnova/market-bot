"""Clock-free input and output models owned by the scalping engine."""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from typing import Self

from pydantic import model_validator

from app.contracts._base import Identifier, PositiveDecimal, StrictFrozenModel
from app.contracts.order_flow import OrderFlowState
from app.contracts.scalp import ScalpAssessment, ScalpTransition


class ScalpContext(StrictFrozenModel):
    """One causal market snapshot; no LONG or Swing thesis is accepted."""

    symbol: Identifier
    as_of: datetime
    current_price: PositiveDecimal
    previous_price: PositiveDecimal
    bid_price: PositiveDecimal
    ask_price: PositiveDecimal
    session_vwap: PositiveDecimal
    atr: PositiveDecimal
    order_flow: OrderFlowState
    support_low: PositiveDecimal | None = None
    support_high: PositiveDecimal | None = None
    previous_assessment: ScalpAssessment | None = None

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if self.bid_price > self.ask_price:
            raise ValueError("bid_price cannot exceed ask_price")
        if self.order_flow.symbol != self.symbol:
            raise ValueError("order flow symbol must match scalp context")
        if self.order_flow.occurred_at > self.as_of:
            raise ValueError("order flow cannot be newer than the scalp context")
        if (self.support_low is None) != (self.support_high is None):
            raise ValueError("support context requires both support levels")
        if (
            self.support_low is not None
            and self.support_high is not None
            and self.support_low > self.support_high
        ):
            raise ValueError("support_low cannot exceed support_high")
        previous = self.previous_assessment
        if previous is not None:
            if previous.symbol != self.symbol:
                raise ValueError("previous assessment symbol must match scalp context")
            if previous.occurred_at > self.as_of:
                raise ValueError("previous assessment cannot be newer than scalp context")
        return self


class ScalpEvaluation(StrictFrozenModel):
    """Current snapshot plus an optional append-only material transition."""

    assessment: ScalpAssessment
    transition: ScalpTransition | None = None

    @model_validator(mode="after")
    def validate_evaluation(self) -> Self:
        if self.transition is not None:
            if self.transition.assessment_id != self.assessment.assessment_id:
                raise ValueError("transition must reference the emitted assessment")
            if self.transition.state is not self.assessment.state:
                raise ValueError("transition state must match the emitted assessment")
        return self


def validate_chronological_contexts(contexts: tuple[ScalpContext, ...]) -> None:
    """Validate an optional replay batch without adding a batch engine API."""

    if any(left.as_of >= right.as_of for left, right in pairwise(contexts)):
        raise ValueError("scalp contexts must be strictly chronological")
    if contexts and any(item.symbol != contexts[0].symbol for item in contexts):
        raise ValueError("scalp replay contexts must belong to one symbol")
