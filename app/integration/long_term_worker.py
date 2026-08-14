"""Independent Long v2 process core with process-local market history."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.common.market_session import is_regular_session
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    UNIVERSE_CHANGED_EVENT,
    AnalysisResult,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    UniverseChanged,
    analysis_result_subject,
)
from app.long_term_engine.models import LongTermContext

from .bar_aggregator import RegularSessionDailyAggregator
from .event_fanout import EventPublisher
from .market_bar_store import MarketBarStore
from .universe_warmup import UniverseWarmupGate

LONG_DAILY_BARS = 260
LONG_WEEKLY_BARS = 220


class LongTermAnalyzer(Protocol):
    def analyze(
        self,
        context: LongTermContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult: ...


class LongTermWorker:
    """Own Long history, evaluate Long v2, and publish only analytical results."""

    def __init__(
        self,
        *,
        publisher: EventPublisher,
        analyzer: LongTermAnalyzer,
    ) -> None:
        self._publisher = publisher
        self._analyzer = analyzer
        self._store = MarketBarStore(capacity_per_series=LONG_DAILY_BARS)
        self._daily_aggregator = RegularSessionDailyAggregator()
        self._universe = UniverseWarmupGate()

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
            elif bar.timeframe in {BarTimeframe.DAY_1, BarTimeframe.WEEK_1}:
                self._store.add(bar)
        return sum([await self._evaluate(symbol) for symbol in _symbols(symbols)])

    async def handle_market_event(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = _bar(envelope)
        if bar.timeframe is BarTimeframe.MINUTE_1:
            if bar.is_final and is_regular_session(bar.timestamp):
                if daily := self._daily_aggregator.add(bar):
                    self._store.add(daily)
                await self._evaluate(
                    bar.symbol,
                    (envelope.event_id,),
                    live_price=bar.close,
                    live_as_of=bar.timestamp,
                )
            return
        if bar.timeframe not in {BarTimeframe.DAY_1, BarTimeframe.WEEK_1}:
            return
        self._store.add(bar)
        if bar.is_final:
            await self._evaluate(bar.symbol, (envelope.event_id,))

    async def _evaluate(
        self,
        symbol: str,
        source_event_ids: tuple[UUID, ...] = (),
        *,
        live_price: Decimal | None = None,
        live_as_of: datetime | None = None,
    ) -> int:
        if not self._universe.allows(symbol):
            return 0
        daily = self._store.history(
            symbol, BarTimeframe.DAY_1, limit=LONG_DAILY_BARS, final_only=True
        )
        weekly = self._store.history(
            symbol, BarTimeframe.WEEK_1, limit=LONG_WEEKLY_BARS, final_only=True
        )
        if not daily or not weekly:
            return 0
        result = self._analyzer.analyze(
            LongTermContext(
                symbol=symbol,
                as_of=max(
                    daily[-1].timestamp,
                    weekly[-1].timestamp,
                    *((live_as_of,) if live_as_of is not None else ()),
                ),
                price=live_price or daily[-1].close,
                daily_bars=daily,
                weekly_bars=weekly,
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
