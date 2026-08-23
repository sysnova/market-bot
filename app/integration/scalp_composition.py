"""Operational paper scalp composition for same-session market context."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from nats.aio.client import Client as NatsClient
from pydantic import BaseModel

from app.common.settings import AppSettings
from app.contracts import (
    MARKET_BAR_EVENT,
    MARKET_QUOTE_EVENT,
    ORDER_FLOW_STATE_EVENT,
    SCALP_ASSESSMENT_EVENT,
    SCALP_TRANSITION_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    EventEnvelope,
    MarketBar,
    MarketQuote,
    OrderFlowState,
    ScalpAssessment,
    Subscription,
    SubscriptionOptions,
    SupportAssessment,
    scalp_assessment_subject,
    scalp_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.event_bus.codec import decode_envelope
from app.scalp_engine import ScalpContext

from .distributed_composition import write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .universe_policy import universe_health_details

_NEW_YORK = ZoneInfo("America/New_York")


class _CoreMessage(Protocol):
    data: bytes


async def run_scalp_process(  # pragma: no cover - long-running NATS process
    *, ready_path: Path | None = None
) -> None:
    """Publish analytical scalp maturity; never create broker orders."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    engine = assembly.build_scalp()
    url = settings.nats_url.get_secret_value()
    bus = await NatsJetStreamEventBus.connect(
        servers=[url], prefix="marketbot", stream="MARKETBOT"
    )
    core = NatsClient()
    await core.connect(url)
    quotes: dict[str, MarketQuote] = {}
    bars: dict[str, deque[MarketBar]] = defaultdict(lambda: deque(maxlen=420))
    supports: dict[str, SupportAssessment] = {}
    previous: dict[str, ScalpAssessment] = {}
    last_prices: dict[str, Decimal] = {}
    subscriptions: list[Subscription] = []

    async def handle_quote(message: _CoreMessage) -> None:
        envelope = decode_envelope(message.data)
        if envelope.event_type != MARKET_QUOTE_EVENT:
            return
        quote = _model(envelope, MarketQuote)
        current = quotes.get(quote.symbol)
        if current is None or quote.occurred_at >= current.occurred_at:
            quotes[quote.symbol] = quote

    async def handle_bar(envelope: EventEnvelope) -> None:
        if envelope.event_type != MARKET_BAR_EVENT:
            return
        bar = _model(envelope, MarketBar)
        if not bar.is_final or bar.timeframe.value != "1Min":
            return
        series = bars[bar.symbol]
        if series and bar.timestamp <= series[-1].timestamp:
            return
        series.append(bar)

    async def handle_support(envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        support = _model(envelope, SupportAssessment)
        supports[support.symbol] = support

    async def handle_order_flow(envelope: EventEnvelope) -> None:
        if envelope.event_type != ORDER_FLOW_STATE_EVENT:
            return
        flow = _model(envelope, OrderFlowState)
        quote = quotes.get(flow.symbol)
        series = bars.get(flow.symbol)
        if quote is None or not series or quote.occurred_at > flow.occurred_at:
            return
        session_bars = _session_bars(tuple(series), flow.occurred_at)
        if not session_bars:
            return
        current_price = flow.current_price
        prior_price = last_prices.get(flow.symbol, session_bars[-1].close)
        support = supports.get(flow.symbol)
        support_low = support.zone_low if support is not None else None
        support_high = support.zone_high if support is not None else None
        evaluation = engine.evaluate(
            ScalpContext(
                symbol=flow.symbol,
                as_of=flow.occurred_at,
                current_price=current_price,
                previous_price=prior_price,
                bid_price=quote.bid_price,
                ask_price=quote.ask_price,
                session_vwap=_session_vwap(session_bars),
                atr=_intraday_atr(session_bars, current_price),
                order_flow=flow,
                support_low=support_low,
                support_high=support_high,
                previous_assessment=previous.get(flow.symbol),
            )
        )
        last_prices[flow.symbol] = current_price
        previous[flow.symbol] = evaluation.assessment
        if evaluation.transition is None:
            return
        assessment = evaluation.assessment
        transition = evaluation.transition
        await bus.publish(
            scalp_assessment_subject(assessment.symbol),
            EventEnvelope(
                event_id=assessment.assessment_id,
                event_type=SCALP_ASSESSMENT_EVENT,
                occurred_at=assessment.occurred_at,
                source="scalp",
                subject=assessment.symbol,
                payload=assessment,
                causation_id=flow.state_id,
            ),
        )
        await bus.publish(
            scalp_transition_subject(transition.state, transition.symbol),
            EventEnvelope(
                event_id=transition.transition_id,
                event_type=SCALP_TRANSITION_EVENT,
                occurred_at=transition.occurred_at,
                source="scalp",
                subject=transition.symbol,
                payload=transition,
                causation_id=flow.state_id,
            ),
        )

    core_subscription = await core.subscribe("marketbot.market.data.quote.>", cb=handle_quote)
    subscriptions.extend(
        [
            await bus.subscribe(
                "marketbot.v1.market.bar.1Min.>",
                handle_bar,
                options=SubscriptionOptions(
                    durable_name="marketbot-scalp-bars-v1",
                    replay_all=False,
                    ack_wait_seconds=30,
                ),
            ),
            await bus.subscribe(
                "marketbot.v1.support-confirmation.assessment.>",
                handle_support,
                options=SubscriptionOptions(replay_latest_per_subject=True),
            ),
            await bus.subscribe(
                "marketbot.v1.order-flow.state.>",
                handle_order_flow,
                options=SubscriptionOptions(
                    durable_name="marketbot-scalp-order-flow-v1",
                    replay_all=False,
                    ack_wait_seconds=30,
                ),
            ),
        ]
    )
    try:
        if ready_path is not None:
            spec = assembly.spec(EngineSlot.SCALP)
            write_ready(
                ready_path,
                {
                    **universe_health_details("scalp"),
                    "service": "scalp",
                    "mode": "PAPER",
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": spec.implementation,
                    "engine_strategy_version": spec.strategy.version,
                },
            )
        await asyncio.Event().wait()
    finally:
        await core_subscription.unsubscribe()
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await core.drain()
        await bus.close()


def _session_bars(series: tuple[MarketBar, ...], as_of: datetime) -> tuple[MarketBar, ...]:
    session_date = as_of.astimezone(_NEW_YORK).date()
    return tuple(
        bar
        for bar in series
        if bar.timestamp.astimezone(_NEW_YORK).date() == session_date
        and bar.timestamp <= as_of
    )


def _session_vwap(series: tuple[MarketBar, ...]) -> Decimal:
    volume = sum((bar.volume for bar in series), Decimal("0"))
    if volume <= 0:
        return series[-1].close
    weighted = sum(
        ((bar.vwap or bar.close) * bar.volume for bar in series),
        Decimal("0"),
    )
    return weighted / volume


def _intraday_atr(series: tuple[MarketBar, ...], price: Decimal) -> Decimal:
    ranges = tuple(bar.high - bar.low for bar in series[-14:] if bar.high > bar.low)
    if ranges:
        return sum(ranges, Decimal("0")) / Decimal(len(ranges))
    return price * Decimal("0.002")


def _model[Model: BaseModel](envelope: EventEnvelope, model: type[Model]) -> Model:
    if isinstance(envelope.payload, model):
        return envelope.payload
    return model.model_validate(envelope.payload, strict=False)
