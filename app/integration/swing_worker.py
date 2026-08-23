"""Independent Swing v2 process core with process-local market history."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.common.market_session import is_regular_analytical_bar, is_regular_session
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    UNIVERSE_CHANGED_EVENT,
    AnalysisResult,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    OrderFlowSupportAssessment,
    SupportAssessment,
    UniverseChanged,
    analysis_result_subject,
)
from app.swing_engine.models import SwingContext

from .bar_aggregator import MinuteBarAggregator, RegularSessionDailyAggregator
from .event_fanout import EventPublisher
from .market_bar_store import MarketBarStore
from .universe_warmup import UniverseWarmupGate

SWING_DAILY_BARS = 120
SWING_INTRADAY_BARS = 160


class SwingAnalyzer(Protocol):
    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult: ...


class SwingWorker:
    """Own Swing history, aggregate live minutes, and emit Swing results."""

    def __init__(
        self,
        *,
        publisher: EventPublisher,
        analyzer: SwingAnalyzer,
    ) -> None:
        self._publisher = publisher
        self._analyzer = analyzer
        self._store = MarketBarStore(capacity_per_series=SWING_INTRADAY_BARS)
        self._aggregator = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_15,))
        self._daily_aggregator = RegularSessionDailyAggregator()
        self._universe = UniverseWarmupGate()
        self._support: dict[str, SupportAssessment] = {}
        self._order_flow_support: dict[str, OrderFlowSupportAssessment] = {}

    def activate_universe(self, symbols: tuple[str, ...]) -> None:
        self._universe.activate(symbols)

    async def handle_universe_event(self, envelope: EventEnvelope) -> int:
        if envelope.event_type != UNIVERSE_CHANGED_EVENT:
            return 0
        change = (
            envelope.payload
            if isinstance(envelope.payload, UniverseChanged)
            else UniverseChanged.model_validate(envelope.payload, strict=False)
        )
        return await self.handle_universe_changed(change)

    async def handle_universe_changed(self, change: UniverseChanged) -> int:
        added = self._universe.apply(change)
        return sum([await self._evaluate(symbol) for symbol in added])

    async def bootstrap(
        self,
        bars: Iterable[MarketBar],
        *,
        symbols: tuple[str, ...],
    ) -> int:
        for bar in bars:
            if bar.timeframe is BarTimeframe.MINUTE_1:
                if daily := self._daily_aggregator.add(bar):
                    self._store.add(daily)
            elif bar.timeframe in {BarTimeframe.DAY_1, BarTimeframe.MINUTE_15} and (
                is_regular_analytical_bar(bar)
            ):
                self._store.add(bar)
        return sum([await self._evaluate(symbol) for symbol in _symbols(symbols)])

    async def handle_market_event(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = _bar(envelope)
        if bar.timeframe is BarTimeframe.MINUTE_1:
            if not is_regular_session(bar.timestamp):
                for aggregated in self._aggregator.add(bar):
                    self._store.add(aggregated)
                    await self._evaluate(bar.symbol, (envelope.event_id,))
                return
            if daily := self._daily_aggregator.add(bar):
                self._store.add(daily)
            for aggregated in self._aggregator.add(bar):
                self._store.add(aggregated)
                await self._evaluate(bar.symbol, (envelope.event_id,))
            return
        if bar.timeframe not in {BarTimeframe.DAY_1, BarTimeframe.MINUTE_15}:
            return
        if bar.timeframe is BarTimeframe.MINUTE_15 and not is_regular_session(bar.timestamp):
            return
        self._store.add(bar)
        if bar.is_final:
            await self._evaluate(bar.symbol, (envelope.event_id,))

    async def handle_support_event(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, SupportAssessment)
            else SupportAssessment.model_validate(envelope.payload, strict=False)
        )
        previous = self._support.get(item.symbol)
        if previous is not None and item.occurred_at < previous.occurred_at:
            return
        self._support[item.symbol] = item
        order_flow_support = self._order_flow_support.get(item.symbol)
        if (
            order_flow_support is not None
            and order_flow_support.support_assessment_id != item.assessment_id
        ):
            self._order_flow_support.pop(item.symbol, None)
        if self._universe.allows(item.symbol):
            await self._evaluate(item.symbol, (envelope.event_id,))

    async def handle_order_flow_support_event(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != ORDER_FLOW_SUPPORT_ASSESSMENT_EVENT:
            return
        item = (
            envelope.payload
            if isinstance(envelope.payload, OrderFlowSupportAssessment)
            else OrderFlowSupportAssessment.model_validate(envelope.payload, strict=False)
        )
        previous = self._order_flow_support.get(item.symbol)
        if previous is not None and item.occurred_at < previous.occurred_at:
            return
        self._order_flow_support[item.symbol] = item
        if self._universe.allows(item.symbol):
            await self._evaluate(
                item.symbol,
                (envelope.event_id,),
                as_of=item.occurred_at,
                current_price=item.current_price,
            )

    async def _evaluate(
        self,
        symbol: str,
        source_event_ids: tuple[UUID, ...] = (),
        *,
        as_of: datetime | None = None,
        current_price: Decimal | None = None,
    ) -> int:
        if not self._universe.allows(symbol):
            return 0
        daily = self._store.history(
            symbol, BarTimeframe.DAY_1, limit=SWING_DAILY_BARS, final_only=True
        )
        intraday = self._store.history(
            symbol,
            BarTimeframe.MINUTE_15,
            limit=SWING_INTRADAY_BARS,
            final_only=True,
        )
        if not daily or not intraday:
            return 0
        effective_as_of = max(daily[-1].timestamp, intraday[-1].timestamp)
        if as_of is not None:
            effective_as_of = max(effective_as_of, as_of)
        effective_price = current_price if current_price is not None else intraday[-1].close
        support = self._support.get(symbol)
        order_flow_support = self._order_flow_support.get(symbol)
        if (
            order_flow_support is not None
            and (
                order_flow_support.occurred_at > effective_as_of
                or support is None
                or order_flow_support.support_assessment_id != support.assessment_id
            )
        ):
            order_flow_support = None
        result = self._analyzer.analyze(
            SwingContext(
                symbol=symbol,
                as_of=effective_as_of,
                price=effective_price,
                daily_bars=daily,
                intraday_bars=intraday,
                support=support,
                order_flow_support=order_flow_support,
            ),
            source_event_ids=source_event_ids,
        )
        await _publish_result(self._publisher, result)
        return 1


def _bar(envelope: EventEnvelope) -> MarketBar:
    if isinstance(envelope.payload, MarketBar):
        return envelope.payload
    return MarketBar.model_validate(envelope.payload, strict=False)


async def _publish_result(publisher: EventPublisher, result: AnalysisResult) -> None:
    await publisher.publish(
        analysis_result_subject(result.horizon, result.symbol),
        EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=result.as_of,
            source=result.engine_id,
            subject=result.symbol,
            payload=result,
        ),
    )


def _symbols(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
