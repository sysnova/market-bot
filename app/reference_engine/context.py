"""Translate stable input envelopes into one immutable evaluation context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from app.contracts import ContextValue, EvaluationContext, EventEnvelope, MarketSession


def context_from_event(envelope: EventEnvelope) -> EvaluationContext:
    """Build the exact context shared by every strategy evaluating an event."""

    if not isinstance(envelope.payload, Mapping):
        raise ValueError("synthetic event payload must be an object")
    payload = cast("Mapping[object, object]", envelope.payload)
    symbol = payload.get("symbol")
    timeframe = payload.get("timeframe")
    values = payload.get("values")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("synthetic event payload requires symbol")
    if not isinstance(timeframe, str) or not timeframe:
        raise ValueError("synthetic event payload requires timeframe")
    if not isinstance(values, Mapping):
        raise ValueError("synthetic event payload requires values object")
    value_map = cast("Mapping[object, object]", values)
    if any(not isinstance(name, str) for name in value_map):
        raise ValueError("synthetic context value names must be strings")

    market_session = envelope.market_session
    if market_session is None:
        raw_session = payload.get("market_session")
        if not isinstance(raw_session, str):
            raise ValueError("synthetic event requires market_session")
        market_session = MarketSession(raw_session)
    run_id_value = payload.get("run_id")
    run_id = run_id_value if isinstance(run_id_value, str) else None
    context_values = tuple(
        ContextValue(name=name, value=value_map[name], observed_at=envelope.occurred_at)
        for name in sorted(cast("list[str]", list(value_map)))
    )
    return EvaluationContext(
        symbol=symbol,
        timeframe=timeframe,
        as_of=envelope.occurred_at,
        market_session=market_session,
        run_id=run_id,
        trace_id=envelope.trace_id,
        correlation_id=envelope.correlation_id,
        values=context_values,
    )
