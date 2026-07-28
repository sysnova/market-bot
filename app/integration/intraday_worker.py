"""Independent Intraday v2 process core with process-local market history."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    AnalysisResult,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    analysis_result_subject,
)
from app.intraday_engine import IntradayEngineV2
from app.intraday_engine.models import IntradayContext

from .bar_aggregator import MinuteBarAggregator
from .event_fanout import EventPublisher
from .market_bar_store import MarketBarStore

INTRADAY_MINUTE_BARS = 500
INTRADAY_FIVE_MINUTE_BARS = 100
_NEW_YORK = ZoneInfo("America/New_York")


class IntradayAnalyzer(Protocol):
    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult: ...


class IntradayWorker:
    """Own minute history, derive five-minute bars, and emit Intraday results."""

    def __init__(
        self,
        *,
        publisher: EventPublisher,
        analyzer: IntradayAnalyzer | None = None,
    ) -> None:
        self._publisher = publisher
        self._analyzer = analyzer or IntradayEngineV2()
        self._store = MarketBarStore(capacity_per_series=INTRADAY_MINUTE_BARS)
        self._aggregator = MinuteBarAggregator(targets=(BarTimeframe.MINUTE_5,))

    async def bootstrap(
        self,
        bars: Iterable[MarketBar],
        *,
        symbols: tuple[str, ...],
    ) -> int:
        for bar in bars:
            if bar.timeframe is not BarTimeframe.MINUTE_1:
                continue
            self._store.add(bar)
            for aggregated in self._aggregator.add(bar):
                self._store.add(aggregated)
        return sum([await self._evaluate(symbol) for symbol in _symbols(symbols)])

    async def handle_market_event(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = _bar(envelope)
        if bar.timeframe is not BarTimeframe.MINUTE_1:
            return
        self._store.add(bar)
        for aggregated in self._aggregator.add(bar):
            self._store.add(aggregated)
        if bar.is_final:
            await self._evaluate(bar.symbol, (envelope.event_id,))

    async def _evaluate(
        self,
        symbol: str,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> int:
        minute = self._store.history(
            symbol,
            BarTimeframe.MINUTE_1,
            limit=INTRADAY_MINUTE_BARS,
            final_only=True,
        )
        if not minute:
            return 0
        session_date = minute[-1].timestamp.astimezone(_NEW_YORK).date()
        session_minutes = tuple(
            item
            for item in minute
            if item.timestamp.astimezone(_NEW_YORK).date() == session_date
        )
        five_minute = self._store.history(
            symbol,
            BarTimeframe.MINUTE_5,
            limit=INTRADAY_FIVE_MINUTE_BARS,
            final_only=True,
        )
        session_five_minute = tuple(
            item
            for item in five_minute
            if item.timestamp.astimezone(_NEW_YORK).date() == session_date
            and item.timestamp <= session_minutes[-1].timestamp
        )
        result = self._analyzer.analyze(
            IntradayContext(
                symbol=symbol,
                as_of=session_minutes[-1].timestamp,
                minute_bars=session_minutes,
                five_minute_bars=session_five_minute,
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
