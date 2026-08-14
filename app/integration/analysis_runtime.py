"""Composition root for market bars, horizon engines, and human-only alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from app.alert_engine import AlertDispatcher, AlertEngine
from app.common.clock import Clock
from app.common.market_session import (
    is_completed_daily_bar,
    is_regular_analytical_bar,
)
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    ENTRY_OPPORTUNITY_EVENT,
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    AnalysisResult,
    BarTimeframe,
    EntryOpportunityEvent,
    EntryWatchTransition,
    EventEnvelope,
    LocalAlert,
    MarketBar,
    analysis_result_subject,
    entry_opportunity_subject,
    market_bar_subject,
)
from app.intraday_engine import IntradayContext
from app.long_term_engine import LongTermContext
from app.swing_engine import SwingContext

from .bar_aggregator import MinuteBarAggregator, RegularSessionDailyAggregator
from .event_fanout import EventPublisher
from .market_bar_store import MarketBarStore

_NEW_YORK = ZoneInfo("America/New_York")


class LongTermAnalyzer(Protocol):
    def analyze(
        self,
        context: LongTermContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult: ...


class SwingAnalyzer(Protocol):
    def analyze(
        self,
        context: SwingContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult: ...


class IntradayAnalyzer(Protocol):
    def analyze(
        self,
        context: IntradayContext,
        *,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> AnalysisResult: ...


class EntryWatchAnalyzer(Protocol):
    async def ingest(
        self, result: AnalysisResult, *, now: datetime
    ) -> EntryWatchTransition | None: ...


class EntryOpportunityAnalyzer(Protocol):
    async def ingest_analysis(
        self, result: AnalysisResult, *, now: datetime
    ) -> tuple[EntryOpportunityEvent, ...]: ...

    async def ingest_transition(
        self, transition: EntryWatchTransition
    ) -> tuple[EntryOpportunityEvent, ...]: ...

    async def ingest_alert(self, alert: LocalAlert) -> tuple[EntryOpportunityEvent, ...]: ...

    async def ingest_bar(self, bar: MarketBar) -> tuple[EntryOpportunityEvent, ...]: ...


class AnalysisRuntime:
    """Keep backfill quiet, then evaluate each horizon on its natural cadence."""

    def __init__(
        self,
        *,
        store: MarketBarStore,
        publisher: EventPublisher,
        long_term: LongTermAnalyzer,
        swing: SwingAnalyzer,
        intraday: IntradayAnalyzer,
        alert_engine: AlertEngine,
        alert_dispatcher: AlertDispatcher,
        clock: Clock,
        aggregator: MinuteBarAggregator | None = None,
        entry_watcher: EntryWatchAnalyzer | None = None,
        entry_opportunity: EntryOpportunityAnalyzer | None = None,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._long_term = long_term
        self._swing = swing
        self._intraday = intraday
        self._alert_engine = alert_engine
        self._alert_dispatcher = alert_dispatcher
        self._clock = clock
        self._aggregator = aggregator or MinuteBarAggregator(
            targets=(BarTimeframe.MINUTE_5, BarTimeframe.MINUTE_15)
        )
        self._daily_aggregator = RegularSessionDailyAggregator()
        self._entry_watcher = entry_watcher
        self._entry_opportunity = entry_opportunity
        self._live = False

    def enable_live(self) -> None:
        self._live = True

    def disable_live(self) -> None:
        """Mute event-driven evaluations while historical context is loading."""
        self._live = False

    async def handle_market_event(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            raise ValueError("analysis runtime accepts only market bar events")
        bar = (
            envelope.payload
            if isinstance(envelope.payload, MarketBar)
            else MarketBar.model_validate(envelope.payload, strict=False)
        )
        if not is_regular_analytical_bar(bar):
            if bar.timeframe is BarTimeframe.MINUTE_1:
                for aggregated in self._aggregator.add(bar):
                    await self._publish_aggregated(aggregated, envelope.event_id)
            return
        if bar.timeframe is BarTimeframe.DAY_1 and not is_completed_daily_bar(
            bar, as_of=self._clock.now()
        ):
            return
        self._store.add(bar)
        if not self._live or not bar.is_final:
            return
        if self._entry_opportunity is not None:
            await self._dispatch_opportunity_events(await self._entry_opportunity.ingest_bar(bar))
        if bar.timeframe is BarTimeframe.MINUTE_1:
            if daily := self._daily_aggregator.add(bar):
                self._store.add(daily)
                await self._evaluate_long_term(bar.symbol, (envelope.event_id,))
            for aggregated in self._aggregator.add(bar):
                await self._publish_aggregated(aggregated, envelope.event_id)
            await self._evaluate_intraday(bar.symbol, (envelope.event_id,))
        elif bar.timeframe is BarTimeframe.MINUTE_15:
            await self._evaluate_swing(bar.symbol, (envelope.event_id,))
        elif bar.timeframe in {BarTimeframe.DAY_1, BarTimeframe.WEEK_1}:
            await self._evaluate_long_term(bar.symbol, (envelope.event_id,))

    async def evaluate_all(self, symbols: tuple[str, ...]) -> None:
        for symbol in tuple(dict.fromkeys(item.strip().upper() for item in symbols)):
            await self._evaluate_long_term(symbol)
            await self._evaluate_swing(symbol)
            await self._evaluate_intraday(symbol)

    async def evaluate_long_term_all(self, symbols: tuple[str, ...]) -> None:
        for symbol in tuple(dict.fromkeys(item.strip().upper() for item in symbols)):
            await self._evaluate_long_term(symbol)

    async def ingest_analysis(self, result: AnalysisResult) -> None:
        envelope = EventEnvelope(
            event_type=ANALYSIS_RESULT_EVENT,
            occurred_at=result.as_of,
            source=result.engine_id,
            subject=result.symbol,
            payload=result,
        )
        await self._publisher.publish(
            analysis_result_subject(result.horizon, result.symbol),
            envelope,
        )
        now = self._clock.now()
        if self._entry_opportunity is not None:
            await self._dispatch_opportunity_events(
                await self._entry_opportunity.ingest_analysis(result, now=now)
            )
        alert = self._alert_engine.ingest(result, now=now)
        if self._entry_watcher is not None:
            transition = await self._entry_watcher.ingest(result, now=now)
            if transition is not None:
                if self._entry_opportunity is not None:
                    await self._dispatch_opportunity_events(
                        await self._entry_opportunity.ingest_transition(transition)
                    )
                await self._alert_dispatcher.dispatch(
                    self._alert_engine.ingest_entry_watch(transition, now=now)
                )
        if alert is not None:
            if self._entry_opportunity is not None:
                await self._dispatch_opportunity_events(
                    await self._entry_opportunity.ingest_alert(alert)
                )
            await self._alert_dispatcher.dispatch(alert)

    async def _dispatch_opportunity_events(self, events: tuple[EntryOpportunityEvent, ...]) -> None:
        for event in events:
            await self._publisher.publish(
                entry_opportunity_subject(
                    event.opportunity.status,
                    event.opportunity.symbol,
                ),
                EventEnvelope(
                    event_type=ENTRY_OPPORTUNITY_EVENT,
                    occurred_at=event.occurred_at,
                    source="entry-opportunity",
                    subject=event.opportunity.symbol,
                    payload=event,
                    causation_id=event.event_id,
                ),
            )
            await self._alert_dispatcher.dispatch(
                self._alert_engine.ingest_entry_opportunity(
                    event,
                    now=self._clock.now(),
                )
            )

    async def _evaluate_long_term(
        self,
        symbol: str,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> None:
        daily = self._store.history(symbol, BarTimeframe.DAY_1, limit=260, final_only=True)
        weekly = self._store.history(symbol, BarTimeframe.WEEK_1, limit=220, final_only=True)
        if not daily or not weekly:
            return
        as_of = max(daily[-1].timestamp, weekly[-1].timestamp)
        result = self._long_term.analyze(
            LongTermContext(
                symbol=symbol,
                as_of=as_of,
                price=daily[-1].close,
                daily_bars=daily,
                weekly_bars=weekly,
            ),
            source_event_ids=source_event_ids,
        )
        await self.ingest_analysis(result)

    async def _evaluate_swing(
        self,
        symbol: str,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> None:
        daily = self._store.history(symbol, BarTimeframe.DAY_1, limit=120, final_only=True)
        intraday = self._store.history(symbol, BarTimeframe.MINUTE_15, limit=160, final_only=True)
        if not daily or not intraday:
            return
        as_of = max(daily[-1].timestamp, intraday[-1].timestamp)
        result = self._swing.analyze(
            SwingContext(
                symbol=symbol,
                as_of=as_of,
                price=intraday[-1].close,
                daily_bars=daily,
                intraday_bars=intraday,
            ),
            source_event_ids=source_event_ids,
        )
        await self.ingest_analysis(result)

    async def _evaluate_intraday(
        self,
        symbol: str,
        source_event_ids: tuple[UUID, ...] = (),
    ) -> None:
        minute = self._store.history(symbol, BarTimeframe.MINUTE_1, limit=500, final_only=True)
        if not minute:
            return
        session_date = minute[-1].timestamp.astimezone(_NEW_YORK).date()
        session_minutes = tuple(
            bar for bar in minute if bar.timestamp.astimezone(_NEW_YORK).date() == session_date
        )
        five_minute = self._store.history(symbol, BarTimeframe.MINUTE_5, limit=100, final_only=True)
        session_five_minute = tuple(
            bar
            for bar in five_minute
            if bar.timestamp.astimezone(_NEW_YORK).date() == session_date
            and bar.timestamp <= session_minutes[-1].timestamp
        )
        result = self._intraday.analyze(
            IntradayContext(
                symbol=symbol,
                as_of=session_minutes[-1].timestamp,
                minute_bars=session_minutes,
                five_minute_bars=session_five_minute,
            ),
            source_event_ids=source_event_ids,
        )
        await self.ingest_analysis(result)

    async def _publish_aggregated(self, bar: MarketBar, causation_id: UUID) -> None:
        await self._publisher.publish(
            market_bar_subject(bar.timeframe, bar.symbol),
            EventEnvelope(
                event_type=MARKET_BAR_EVENT,
                occurred_at=bar.timestamp,
                source="marketbot-aggregator",
                causation_id=causation_id,
                subject=bar.symbol,
                payload=bar,
            ),
        )
