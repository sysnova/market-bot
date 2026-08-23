"""Live Order Flow composition over the existing Alpaca Core NATS hot path."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

from nats.aio.client import Client as NatsClient
from pydantic import BaseModel

from app.common.settings import AppSettings
from app.contracts import (
    MARKET_QUOTE_EVENT,
    MARKET_TRADE_CANCEL_EVENT,
    MARKET_TRADE_CORRECTION_EVENT,
    MARKET_TRADE_EVENT,
    ORDER_FLOW_STATE_EVENT,
    ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT,
    ORDER_FLOW_TRANSITION_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    EventEnvelope,
    MarketQuote,
    MarketTrade,
    MarketTradeCancel,
    MarketTradeCorrection,
    OrderFlowState,
    SubscriptionOptions,
    SupportAssessment,
    order_flow_state_subject,
    order_flow_support_subject,
    order_flow_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.event_bus.codec import decode_envelope
from app.order_flow_engine import OrderFlowUpdate, assess_support_order_flow

from .distributed_composition import write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .universe_policy import universe_health_details


class _CoreMessage(Protocol):
    data: bytes


_STATE_PUBLISH_INTERVAL = timedelta(seconds=1)
_SUPPORT_REFRESH_INTERVAL = timedelta(seconds=15)


async def run_order_flow_process(  # pragma: no cover - long-running NATS process
    *, ready_path: Path | None = None
) -> None:
    """Consume typed ticks and publish operational analytical state."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    engine = assembly.build_order_flow()
    url = settings.nats_url.get_secret_value()
    core = NatsClient()
    await core.connect(url)
    durable = await NatsJetStreamEventBus.connect(
        servers=[url], prefix="marketbot", stream="MARKETBOT"
    )
    supports: dict[str, SupportAssessment] = {}
    last_state_at: dict[str, datetime] = {}
    last_support_at: dict[str, datetime] = {}
    last_support_signature: dict[str, tuple[object, ...]] = {}

    async def handle_support(envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        support = _payload(envelope, SupportAssessment)
        previous = supports.get(support.symbol)
        if previous is None or support.occurred_at >= previous.occurred_at:
            supports[support.symbol] = support

    async def publish_update(update: OrderFlowUpdate, *, causation_id: UUID) -> None:
        state = update.state
        transition = update.transition
        previous_state_at = last_state_at.get(state.symbol)
        publish_state = (
            transition is not None
            or previous_state_at is None
            or state.occurred_at - previous_state_at >= _STATE_PUBLISH_INTERVAL
        )
        if publish_state:
            last_state_at[state.symbol] = state.occurred_at
            await durable.publish(
                order_flow_state_subject(state.symbol),
                EventEnvelope(
                    event_id=state.state_id,
                    event_type=ORDER_FLOW_STATE_EVENT,
                    occurred_at=state.occurred_at,
                    source="order-flow",
                    subject=state.symbol,
                    payload=state,
                    causation_id=causation_id,
                ),
            )
            await publish_support_assessment(state, causation_id=causation_id)
        if transition is not None:
            await durable.publish(
                order_flow_transition_subject(transition.state, transition.symbol),
                EventEnvelope(
                    event_id=transition.transition_id,
                    event_type=ORDER_FLOW_TRANSITION_EVENT,
                    occurred_at=transition.occurred_at,
                    source="order-flow",
                    subject=transition.symbol,
                    payload=transition,
                    causation_id=causation_id,
                ),
            )

    async def publish_support_assessment(
        state: OrderFlowState, *, causation_id: UUID
    ) -> None:
        support = supports.get(state.symbol)
        if support is None:
            return
        try:
            assessment = assess_support_order_flow(
                state,
                support,
                as_of=state.occurred_at,
            )
        except ValueError:
            return
        signature = (
            assessment.disposition,
            assessment.order_flow_state,
            assessment.support_assessment_id,
        )
        previous_at = last_support_at.get(state.symbol)
        should_publish = (
            signature != last_support_signature.get(state.symbol)
            or previous_at is None
            or assessment.occurred_at - previous_at >= _SUPPORT_REFRESH_INTERVAL
        )
        if not should_publish:
            return
        last_support_signature[state.symbol] = signature
        last_support_at[state.symbol] = assessment.occurred_at
        await durable.publish(
            order_flow_support_subject(assessment.symbol),
            EventEnvelope(
                event_id=assessment.assessment_id,
                event_type=ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT,
                occurred_at=assessment.occurred_at,
                source="order-flow",
                subject=assessment.symbol,
                payload=assessment,
                causation_id=causation_id,
            ),
        )

    async def handle(message: _CoreMessage) -> None:
        envelope = decode_envelope(message.data)
        update = None
        if envelope.event_type == MARKET_QUOTE_EVENT:
            engine.ingest_quote(_payload(envelope, MarketQuote))
        elif envelope.event_type == MARKET_TRADE_EVENT:
            update = engine.ingest_trade(_payload(envelope, MarketTrade))
        elif envelope.event_type == MARKET_TRADE_CORRECTION_EVENT:
            update = engine.apply_correction(_payload(envelope, MarketTradeCorrection))
        elif envelope.event_type == MARKET_TRADE_CANCEL_EVENT:
            update = engine.apply_cancel(_payload(envelope, MarketTradeCancel))
        if update is not None:
            await publish_update(update, causation_id=envelope.event_id)

    subscriptions = [
        await core.subscribe("marketbot.market.data.quote.>", cb=handle),
        await core.subscribe("marketbot.market.data.trade.>", cb=handle),
        await core.subscribe("marketbot.market.data.trade-correction.>", cb=handle),
        await core.subscribe("marketbot.market.data.trade-cancel.>", cb=handle),
    ]
    support_subscription = await durable.subscribe(
        "marketbot.v1.support-confirmation.assessment.>",
        handle_support,
        options=SubscriptionOptions(replay_latest_per_subject=True),
    )
    try:
        if ready_path is not None:
            spec = assembly.spec(EngineSlot.ORDER_FLOW)
            write_ready(
                ready_path,
                {
                    **universe_health_details("order-flow"),
                    "service": "order-flow",
                    "ephemeral_input": True,
                    "output": "durable-compact-state",
                    "mode": "ACTIVE",
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": spec.implementation,
                    "engine_strategy_version": spec.strategy.version,
                },
            )
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await support_subscription.unsubscribe()
        await core.drain()
        await durable.close()


def _payload[Model: BaseModel](envelope: EventEnvelope, model: type[Model]) -> Model:
    if isinstance(envelope.payload, model):
        return envelope.payload
    return model.model_validate(envelope.payload, strict=False)
