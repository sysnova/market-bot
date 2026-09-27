"""Contract-only wiring of isolated SHORT diagnostics; no entry publication path."""

import asyncio
from collections.abc import MutableMapping
from datetime import datetime
from typing import Protocol, cast

from pydantic import BaseModel
from redis import Redis

from app.common.clock import Clock, SystemClock
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    EXECUTION_QUOTE_EVENT,
    ORDER_FLOW_STATE_EVENT,
    SHORT_OBSERVATION_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    AnalysisResult,
    EventEnvelope,
    ExecutionQuoteSnapshot,
    NamedValue,
    OrderFlowState,
    SupportAssessment,
    short_observation_subject,
)
from app.leveraged_thesis_engine.v14 import (
    LeveragedThesisEngineV14,
    ShortObservationContext,
    TacticalState,
)

from .event_fanout import EventPublisher


class TacticalStateStore(Protocol):
    async def get(self, symbol: str) -> TacticalState: ...
    async def put(self, symbol: str, state: TacticalState) -> None: ...


class RedisTacticalStateStore:
    """Keep setup identity separate from disposable analytical context views."""

    def __init__(self, redis: Redis, namespace: str) -> None:
        self.redis, self.prefix = redis, namespace + "short-observation:v1:"

    async def get(self, symbol: str) -> TacticalState:
        raw = await asyncio.to_thread(self.redis.get, self.prefix + symbol)
        return TacticalState.model_validate_json(cast(str | bytes, raw)) if raw else TacticalState()

    async def put(self, symbol: str, state: TacticalState) -> None:
        await asyncio.to_thread(
            self.redis.set, self.prefix + symbol, state.model_dump_json(), ex=172800
        )


class ShortObservationRuntime:
    def __init__(
        self,
        engine: LeveragedThesisEngineV14,
        publisher: EventPublisher,
        *,
        clock: Clock | None = None,
        states: MutableMapping[str, TacticalState] | None = None,
        store: TacticalStateStore | None = None,
    ) -> None:
        self.engine, self.publisher = engine, publisher
        self.clock = clock or SystemClock()
        self.store = store
        self.states: MutableMapping[str, TacticalState] = states if states is not None else {}
        self.analyses: dict[tuple[str, str], AnalysisResult] = {}
        self.supports: dict[str, SupportAssessment] = {}
        self.quotes: dict[str, ExecutionQuoteSnapshot] = {}
        self.quote_received: dict[str, datetime] = {}
        self.flows: dict[str, OrderFlowState] = {}
        self.signatures: dict[tuple[str, str], tuple[object, ...]] = {}
        self.lock = asyncio.Lock()

    async def restore(self) -> None:
        if self.store is not None:
            async with self.lock:
                for pair in self.engine.pairs:
                    self.states[pair.underlying_symbol] = await self.store.get(
                        pair.underlying_symbol
                    )

    async def handle(self, envelope: EventEnvelope) -> None:
        async with self.lock:
            symbol = ""
            if envelope.event_type == ANALYSIS_RESULT_EVENT:
                a = _payload(envelope, AnalysisResult)
                if a.symbol not in self.engine.required_symbols or a.as_of > self.clock.now():
                    return
                key = (a.symbol, a.horizon.value)
                old = self.analyses.get(key)
                if old is not None and old.as_of > a.as_of:
                    return
                self.analyses[key] = a
                symbol = a.symbol
            elif envelope.event_type == EXECUTION_QUOTE_EVENT:
                q = _payload(envelope, ExecutionQuoteSnapshot)
                symbol = q.quote.symbol
                if symbol not in self.engine.required_symbols:
                    return
                old_quote = self.quotes.get(symbol)
                if q.published_at > self.clock.now() or (
                    old_quote is not None and old_quote.quote.occurred_at >= q.quote.occurred_at
                ):
                    return
                self.quotes[symbol] = q
                self.quote_received[symbol] = self.clock.now()
            elif envelope.event_type == SUPPORT_ASSESSMENT_EVENT:
                s = _payload(envelope, SupportAssessment)
                symbol = s.symbol
                if (
                    symbol not in self.engine.required_symbols
                    or (s.assessed_at or s.occurred_at) > self.clock.now()
                ):
                    return
                old_support = self.supports.get(symbol)
                if old_support is not None and (
                    old_support.assessed_at or old_support.occurred_at
                ) > (s.assessed_at or s.occurred_at):
                    return
                self.supports[symbol] = s
            elif envelope.event_type == ORDER_FLOW_STATE_EVENT:
                f = _payload(envelope, OrderFlowState)
                symbol = f.symbol
                if symbol not in self.engine.required_symbols or f.occurred_at > self.clock.now():
                    return
                old_flow = self.flows.get(symbol)
                if old_flow is not None and old_flow.occurred_at >= f.occurred_at:
                    return
                self.flows[symbol] = f
            else:
                return
            for pair in self.engine.pairs:
                if symbol in {pair.underlying_symbol, pair.bearish_instrument}:
                    await self._evaluate(pair.underlying_symbol, pair.bearish_instrument)

    async def tick(self) -> None:
        async with self.lock:
            for pair in self.engine.pairs:
                if (pair.underlying_symbol, "INTRADAY") in self.analyses:
                    await self._evaluate(pair.underlying_symbol, pair.bearish_instrument)

    async def _evaluate(self, symbol: str, instrument: str) -> None:
        intraday = self.analyses.get((symbol, "INTRADAY"))
        if intraday is None:
            return
        context = ShortObservationContext(
            symbol=symbol,
            now=self.clock.now(),
            intraday=intraday,
            swing=self.analyses.get((symbol, "SWING")),
            support=self.supports.get(symbol),
            underlying_quote=self.quotes.get(symbol),
            instrument_quote=self.quotes.get(instrument),
            instrument_flow=self.flows.get(instrument),
            instrument_analysis=self.analyses.get((instrument, "INTRADAY")),
        )
        old = self.states.get(symbol, TacticalState())
        state, tactical = self.engine.observe_tactical(old, context)
        if state != old:
            if self.store is not None:
                await self.store.put(symbol, state)
            self.states[symbol] = state
        for report in (self.engine.inspect_daily(context), tactical):
            signature = (report.setup_id, report.status, report.gates, intraday.as_of)
            key = (symbol, report.route)
            if self.signatures.get(key) == signature:
                continue
            report = report.model_copy(
                update={
                    "metrics": (
                        *report.metrics,
                        NamedValue(
                            name="underlying_quote_consumer_received_at",
                            value=self.quote_received.get(symbol),
                        ),
                        NamedValue(
                            name="instrument_quote_consumer_received_at",
                            value=self.quote_received.get(instrument),
                        ),
                    )
                }
            )
            await self.publisher.publish(
                short_observation_subject(report.route, symbol),
                EventEnvelope(
                    event_type=SHORT_OBSERVATION_EVENT,
                    occurred_at=report.occurred_at,
                    source="leveraged-thesis-observer",
                    subject=symbol,
                    payload=report,
                ),
            )
            self.signatures[key] = signature


def _payload[Model: BaseModel](envelope: EventEnvelope, model: type[Model]) -> Model:
    return (
        envelope.payload
        if isinstance(envelope.payload, model)
        else model.model_validate(envelope.payload, strict=False)
    )
