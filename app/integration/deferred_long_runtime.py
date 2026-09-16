"""Durable pending ASTX LONG intents; independent of disposable engine cache views."""

import asyncio
from typing import Protocol, cast

from redis import Redis

from app.common.clock import Clock, SystemClock
from app.contracts import (
    ENTRY_SIGNAL_EVENT,
    LEVERAGED_THESIS_ASSESSMENT_EVENT,
    LEVERAGED_THESIS_TRANSITION_EVENT,
    ORDER_FLOW_STATE_EVENT,
    EntrySignal,
    EventEnvelope,
    LeveragedThesisTransition,
    OrderFlowState,
    leveraged_thesis_assessment_subject,
    leveraged_thesis_transition_subject,
)
from app.leveraged_thesis_engine.v11 import deferred_event_id
from app.leveraged_thesis_engine.v12 import (
    DeferredLongState,
    LeveragedThesisEngineV12,
)

from .entry_signal_adapter import entry_signal_from_leveraged_thesis, publish_entry_signal
from .event_fanout import EventPublisher


class LongStateStore(Protocol):
    async def get(self, symbol: str) -> DeferredLongState: ...
    async def put(self, symbol: str, state: DeferredLongState) -> None: ...


class RedisLongStateStore:
    def __init__(self, redis: Redis, namespace: str) -> None:
        self.redis, self.namespace = redis, namespace + "pending-long:v1:"

    async def get(self, symbol: str) -> DeferredLongState:
        raw = await asyncio.to_thread(self.redis.get, self.namespace + symbol)
        return (
            DeferredLongState.model_validate_json(cast(str | bytes, raw))
            if raw
            else DeferredLongState()
        )

    async def put(self, symbol: str, state: DeferredLongState) -> None:
        await asyncio.to_thread(
            self.redis.set,
            self.namespace + symbol,
            state.model_dump_json(exclude_computed_fields=True),
            ex=172800,
        )


class DeferredLongRuntime:
    def __init__(
        self,
        engine: LeveragedThesisEngineV12,
        publisher: EventPublisher,
        store: LongStateStore,
        clock: Clock | None = None,
    ) -> None:
        self.engine, self.publisher, self.store = engine, publisher, store
        self.clock = clock or SystemClock()
        self.states: dict[str, DeferredLongState] = {}
        self.lock = asyncio.Lock()

    async def restore(self) -> None:
        async with self.lock:
            self.states["ASTS"] = await self.store.get("ASTS")
        await self.tick()

    async def handle(self, envelope: EventEnvelope) -> None:
        signal = None
        flow = None
        if envelope.event_type == ENTRY_SIGNAL_EVENT:
            signal = (
                envelope.payload
                if isinstance(envelope.payload, EntrySignal)
                else EntrySignal.model_validate(envelope.payload, strict=False)
            )
            symbol = signal.symbol
        elif envelope.event_type == ORDER_FLOW_STATE_EVENT:
            flow = (
                envelope.payload
                if isinstance(envelope.payload, OrderFlowState)
                else OrderFlowState.model_validate(envelope.payload, strict=False)
            )
            symbol = flow.symbol
        else:
            return
        if symbol not in {"ASTS", "ASTX"}:
            return
        async with self.lock:
            old = self.states.get("ASTS", DeferredLongState())
            state = self.engine.advance_long(old, now=self.clock.now(), signal=signal, flow=flow)
            await self._save_and_publish("ASTS", state)

    async def tick(self) -> None:
        async with self.lock:
            for symbol, old in tuple(self.states.items()):
                if (
                    old.intent is not None
                    and old.assessment is not None
                    and old.assessment.state.value not in {"BUY_CONFIRMED", "CANCELLED"}
                ):
                    await self._save_and_publish(
                        symbol, self.engine.advance_long(old, now=self.clock.now())
                    )
                else:
                    await self._save_and_publish(symbol, old)

    async def _save_and_publish(self, symbol: str, state: DeferredLongState) -> None:
        # Persist a confirmed decision before publishing; retries reuse the same IDs.
        await self.store.put(symbol, state)
        self.states[symbol] = state
        item = state.assessment
        if item is None or state.intent is None:
            return
        signature = f"{state.intent.signal.signal_id}:{item.state}:{item.reasons}"
        if state.published_signature == signature:
            return
        await self.publisher.publish(
            leveraged_thesis_assessment_subject(symbol),
            EventEnvelope(
                event_id=item.assessment_id,
                event_type=LEVERAGED_THESIS_ASSESSMENT_EVENT,
                occurred_at=item.occurred_at,
                source="leveraged-thesis",
                subject=symbol,
                payload=item,
                causation_id=state.intent.signal.signal_id,
            ),
        )
        transition = LeveragedThesisTransition(
            transition_id=deferred_event_id(item.occurred_at, f"transition:{item.assessment_id}"),
            assessment_id=item.assessment_id,
            underlying_symbol=symbol,
            instrument_symbol=item.instrument_symbol,
            occurred_at=item.occurred_at,
            engine_version=item.engine_version,
            state=item.state,
            direction=item.direction,
            exposure=item.exposure,
            reference_price=item.underlying_price,
            reasons=item.reasons,
            context_hash=item.context_hash,
        )
        await self.publisher.publish(
            leveraged_thesis_transition_subject(item.state, symbol),
            EventEnvelope(
                event_id=transition.transition_id,
                event_type=LEVERAGED_THESIS_TRANSITION_EVENT,
                occurred_at=item.occurred_at,
                source="leveraged-thesis",
                subject=symbol,
                payload=transition,
                causation_id=state.intent.signal.signal_id,
            ),
        )
        signal = entry_signal_from_leveraged_thesis(item)
        if signal is not None:
            signal = signal.model_copy(
                update={
                    "signal_id": deferred_event_id(item.occurred_at, f"entry:{item.assessment_id}"),
                    "policy_version": self.engine.engine_version,
                    "source_event_ids": tuple(
                        dict.fromkeys((*signal.source_event_ids, state.intent.signal.signal_id))
                    ),
                }
            )
            await publish_entry_signal(self.publisher, signal, source="leveraged-thesis")
        state = state.model_copy(update={"published_signature": signature})
        await self.store.put(symbol, state)
        self.states[symbol] = state
