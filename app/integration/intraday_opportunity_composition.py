"""Durable paper lifecycle driven only by scalp maturity and executable quotes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from nats.aio.client import Client as NatsClient

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import (
    INTRADAY_OPPORTUNITY_TRANSITION_EVENT,
    MARKET_QUOTE_EVENT,
    SCALP_ASSESSMENT_EVENT,
    EventEnvelope,
    IntradayCloseReason,
    IntradayOpportunityEvent,
    IntradayOpportunityEventKind,
    IntradayOpportunityStatus,
    IntradaySide,
    MarketQuote,
    ScalpAssessment,
    ScalpDirection,
    ScalpExitReason,
    ScalpState,
    SubscriptionOptions,
    intraday_opportunity_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.event_bus.codec import decode_envelope
from app.intraday_opportunity_engine import ActiveIntradayOpportunityError
from app.persistence import create_database_engine, create_session_factory

from .distributed_composition import write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .intraday_opportunity_store import PostgresIntradayOpportunityStore
from .universe_policy import universe_health_details

_NEW_YORK = ZoneInfo("America/New_York")
_STRATEGY_ID = "scalp-v1"
_MARK_INTERVAL = timedelta(seconds=1)


class _CoreMessage(Protocol):
    data: bytes


async def run_intraday_opportunity_process(  # pragma: no cover - requires local PostgreSQL/NATS
    *, ready_path: Path | None = None
) -> None:
    """Track paper fills and P/L; this process has no Trading API capability."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresIntradayOpportunityStore(create_session_factory(database))
    if not await store.is_ready():
        await database.dispose()
        raise RuntimeError("intraday opportunity migration is not applied")
    engine = assembly.build_intraday_opportunity(store=store)
    url = settings.nats_url.get_secret_value()
    bus = await NatsJetStreamEventBus.connect(
        servers=[url], prefix="marketbot", stream="MARKETBOT"
    )
    core = NatsClient()
    await core.connect(url)
    now = SystemClock().now()
    session_date = now.astimezone(_NEW_YORK).date()
    current = await store.list_session(session_date)
    active_symbols = {
        item.symbol for item in current if item.status is IntradayOpportunityStatus.OPEN
    }
    last_mark_at: dict[str, datetime] = {}

    async def publish(event: IntradayOpportunityEvent) -> None:
        opportunity = event.opportunity
        await bus.publish(
            intraday_opportunity_subject(opportunity.status, opportunity.symbol),
            EventEnvelope(
                event_id=event.event_id,
                event_type=INTRADAY_OPPORTUNITY_TRANSITION_EVENT,
                occurred_at=event.occurred_at,
                source="intraday-opportunity",
                subject=opportunity.symbol,
                payload=event,
                causation_id=event.source_event_id,
            ),
        )
        if event.kind is IntradayOpportunityEventKind.CLOSED:
            active_symbols.discard(opportunity.symbol)
        else:
            active_symbols.add(opportunity.symbol)

    async def handle_scalp(envelope: EventEnvelope) -> None:
        if envelope.event_type != SCALP_ASSESSMENT_EVENT:
            return
        assessment = _scalp_assessment(envelope)
        event = None
        if assessment.state is ScalpState.ENTRY_CONFIRMED:
            if (
                assessment.entry_price is None
                or assessment.invalidation is None
                or assessment.target is None
                or assessment.max_hold_seconds is None
                or assessment.direction is ScalpDirection.NONE
            ):
                return
            side = (
                IntradaySide.LONG
                if assessment.direction is ScalpDirection.LONG
                else IntradaySide.SHORT
            )
            quantity = (
                settings.intraday_paper_notional / assessment.entry_price
            ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            if quantity <= 0:
                return
            try:
                event = await engine.open_position(
                    source_event_id=assessment.assessment_id,
                    symbol=assessment.symbol,
                    strategy_id=_STRATEGY_ID,
                    side=side,
                    quantity=quantity,
                    bid=assessment.bid_price,
                    ask=assessment.ask_price,
                    stop_price=assessment.invalidation,
                    target_price=assessment.target,
                    occurred_at=assessment.occurred_at,
                    max_holding=timedelta(seconds=assessment.max_hold_seconds),
                )
            except ActiveIntradayOpportunityError:
                return
        elif assessment.state is ScalpState.EXIT_CONFIRMED:
            event = await engine.close_position(
                source_event_id=assessment.assessment_id,
                symbol=assessment.symbol,
                strategy_id=_STRATEGY_ID,
                bid=assessment.bid_price,
                ask=assessment.ask_price,
                occurred_at=assessment.occurred_at,
                reason=_close_reason(assessment.exit_reason),
            )
        if event is not None:
            await publish(event)

    async def handle_quote(message: _CoreMessage) -> None:
        envelope = decode_envelope(message.data)
        if envelope.event_type != MARKET_QUOTE_EVENT:
            return
        quote = _quote(envelope)
        if quote.symbol not in active_symbols:
            return
        previous_mark = last_mark_at.get(quote.symbol)
        if previous_mark is not None and quote.occurred_at - previous_mark < _MARK_INTERVAL:
            return
        last_mark_at[quote.symbol] = quote.occurred_at
        event = await engine.mark_quote(
            source_event_id=quote.event_id,
            symbol=quote.symbol,
            strategy_id=_STRATEGY_ID,
            bid=quote.bid_price,
            ask=quote.ask_price,
            occurred_at=quote.occurred_at,
        )
        if event is not None:
            await publish(event)

    scalp_subscription = await bus.subscribe(
        "marketbot.v1.scalp.assessment.>",
        handle_scalp,
        options=SubscriptionOptions(
            durable_name="marketbot-intraday-opportunity-scalp-v1",
            replay_all=False,
            ack_wait_seconds=60,
        ),
    )
    quote_subscription = await core.subscribe(
        "marketbot.market.data.quote.>", cb=handle_quote
    )
    try:
        if ready_path is not None:
            spec = assembly.spec(EngineSlot.INTRADAY_OPPORTUNITY)
            write_ready(
                ready_path,
                {
                    **universe_health_details("intraday-opportunity"),
                    "service": "intraday-opportunity",
                    "mode": "PAPER",
                    "active_positions": len(active_symbols),
                    "paper_notional": str(settings.intraday_paper_notional),
                    "marketbot_definition_version": assembly.definition.version,
                    "engine_implementation": spec.implementation,
                    "engine_strategy_version": spec.strategy.version,
                },
            )
        await asyncio.Event().wait()
    finally:
        await scalp_subscription.unsubscribe()
        await quote_subscription.unsubscribe()
        await core.drain()
        await bus.close()
        await database.dispose()


def _close_reason(reason: ScalpExitReason | None) -> IntradayCloseReason:
    if reason is None:
        return IntradayCloseReason.MANUAL
    mapping = {
        ScalpExitReason.STOP: IntradayCloseReason.STOP,
        ScalpExitReason.TARGET: IntradayCloseReason.TARGET,
        ScalpExitReason.MAX_HOLD: IntradayCloseReason.TIME_EXIT,
        ScalpExitReason.ORDER_FLOW_REVERSAL: IntradayCloseReason.FLOW_REVERSAL,
    }
    return mapping.get(reason, IntradayCloseReason.MANUAL)


def _scalp_assessment(envelope: EventEnvelope) -> ScalpAssessment:
    if isinstance(envelope.payload, ScalpAssessment):
        return envelope.payload
    return ScalpAssessment.model_validate(envelope.payload, strict=False)


def _quote(envelope: EventEnvelope) -> MarketQuote:
    if isinstance(envelope.payload, MarketQuote):
        return envelope.payload
    return MarketQuote.model_validate(envelope.payload, strict=False)
