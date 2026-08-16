"""Holdings-only Support Confirmation composition over local infrastructure."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.common.clock import Clock, SystemClock
from app.common.market_session import is_regular_analytical_bar
from app.common.settings import AppSettings, Environment
from app.contracts import (
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    SUPPORT_ASSESSMENT_EVENT,
    SUPPORT_TRANSITION_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    Subscription,
    SubscriptionOptions,
    SupportAssessment,
    SupportTransition,
    support_assessment_subject,
    support_transition_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.persistence import create_database_engine
from app.support_confirmation_engine import SupportContext

from .distributed_composition import (
    HistoryRequest,
    connect_nats,
    write_ready,
)
from .engine_assembly import EngineSlot, MarketBotAssembly
from .market_bar_store import MarketBarStore
from .market_history_composition import load_market_history
from .postgres_universe import PostgresUniverseClient, UniverseSnapshot
from .universe_policy import universe_health_details

SUPPORT_HISTORY_REQUESTS = (
    HistoryRequest(
        timeframe=BarTimeframe.DAY_1, lookback=timedelta(days=800), max_bars_per_symbol=520
    ),
    HistoryRequest(
        timeframe=BarTimeframe.WEEK_1, lookback=timedelta(days=365 * 8), max_bars_per_symbol=420
    ),
    HistoryRequest(
        timeframe=BarTimeframe.HOUR_1, lookback=timedelta(days=90), max_bars_per_symbol=500
    ),
)


class HoldingsProvider(Protocol):
    async def get_holdings(self) -> UniverseSnapshot: ...


class SupportPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class SupportEngine(Protocol):
    def evaluate(self, context: SupportContext) -> SupportAssessment: ...


async def load_support_holdings(provider: HoldingsProvider) -> UniverseSnapshot:
    """Resolve active local positions without consulting the watchlist."""

    snapshot = await provider.get_holdings()
    if not snapshot.symbols:
        raise RuntimeError("Support Confirmation requires a positive local holding")
    return snapshot


class SupportConfirmationRuntime:
    def __init__(
        self,
        *,
        engine: SupportEngine,
        publisher: SupportPublisher,
        clock: Clock | None = None,
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._clock = clock or SystemClock()
        self._bars = MarketBarStore(capacity_per_series=650)
        self._symbols: set[str] = set()
        self._latest: dict[str, SupportAssessment] = {}

    async def restore_assessment(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != SUPPORT_ASSESSMENT_EVENT:
            return
        assessment = (
            envelope.payload
            if isinstance(envelope.payload, SupportAssessment)
            else SupportAssessment.model_validate(envelope.payload, strict=False)
        )
        previous = self._latest.get(assessment.symbol)
        if previous is None or assessment.occurred_at >= previous.occurred_at:
            self._latest[assessment.symbol] = assessment

    async def bootstrap(self, bars: Iterable[MarketBar], *, symbols: tuple[str, ...]) -> int:
        self._symbols = {symbol.strip().upper() for symbol in symbols}
        for bar in bars:
            if bar.symbol in self._symbols:
                self._bars.add(bar)
        published = 0
        for symbol in sorted(self._symbols):
            if await self._evaluate(symbol):
                published += 1
        return published

    async def handle_market(self, envelope: EventEnvelope) -> None:
        if envelope.event_type not in {MARKET_BAR_EVENT, MARKET_BAR_UPDATED_EVENT}:
            return
        bar = (
            envelope.payload
            if isinstance(envelope.payload, MarketBar)
            else MarketBar.model_validate(envelope.payload, strict=False)
        )
        if not bar.is_final or bar.symbol not in self._symbols:
            return
        if not is_regular_analytical_bar(bar):
            return
        if bar.timeframe not in {
            BarTimeframe.DAY_1,
            BarTimeframe.WEEK_1,
            BarTimeframe.HOUR_1,
        }:
            return
        self._bars.add(bar)
        if bar.timeframe is BarTimeframe.DAY_1:
            await self._evaluate(bar.symbol)

    async def _evaluate(self, symbol: str) -> bool:
        daily = self._bars.history(symbol, BarTimeframe.DAY_1, limit=520, final_only=True)
        if len(daily) < 15:
            return False
        weekly = self._bars.history(symbol, BarTimeframe.WEEK_1, limit=420, final_only=True)
        hourly = self._bars.history(symbol, BarTimeframe.HOUR_1, limit=500, final_only=True)
        previous = self._latest.get(symbol)
        raw_assessment = self._engine.evaluate(
            SupportContext(
                symbol=symbol,
                daily_bars=daily,
                weekly_bars=weekly,
                hourly_bars=hourly,
                previous_assessment=previous,
            )
        )
        assessment = _stamp_assessment(raw_assessment, self._clock.now())
        if previous is not None and _same_observation(previous, assessment):
            return False
        self._latest[symbol] = assessment
        await self._publish_assessment(assessment)
        if previous is None or previous.state is not assessment.state:
            await self._publish_transition(assessment, previous)
        return True

    async def _publish_assessment(self, assessment: SupportAssessment) -> None:
        await self._publisher.publish(
            support_assessment_subject(assessment.symbol),
            EventEnvelope(
                event_type=SUPPORT_ASSESSMENT_EVENT,
                occurred_at=assessment.assessed_at or assessment.occurred_at,
                source="support-confirmation-v0",
                subject=assessment.symbol,
                payload=assessment,
            ),
        )

    async def _publish_transition(
        self, assessment: SupportAssessment, previous: SupportAssessment | None
    ) -> None:
        transition = SupportTransition(
            assessment_id=assessment.assessment_id,
            symbol=assessment.symbol,
            occurred_at=assessment.assessed_at or assessment.occurred_at,
            engine_version=assessment.engine_version,
            previous_state=previous.state if previous is not None else None,
            state=assessment.state,
            confirmation_type=assessment.confirmation_type,
            support_score=assessment.support_score,
            reaction_score=assessment.reaction_score,
            reversal_score=assessment.reversal_score,
            zone_low=assessment.zone_low,
            zone_high=assessment.zone_high,
            invalidation=assessment.invalidation,
            reasons=assessment.reasons,
            context_hash=assessment.context_hash,
        )
        await self._publisher.publish(
            support_transition_subject(transition.state, transition.symbol),
            EventEnvelope(
                event_type=SUPPORT_TRANSITION_EVENT,
                occurred_at=transition.occurred_at,
                source="support-confirmation-v0",
                subject=transition.symbol,
                payload=transition,
            ),
        )


def _same_observation(previous: SupportAssessment, current: SupportAssessment) -> bool:
    return (
        previous.context_hash == current.context_hash
        and previous.engine_version == current.engine_version
        and previous.state is current.state
        and previous.confirmation_type is current.confirmation_type
        and previous.support_score == current.support_score
        and previous.reaction_score == current.reaction_score
        and previous.reversal_score == current.reversal_score
        and previous.b_wave_risk is current.b_wave_risk
        and previous.current_price == current.current_price
        and previous.structural_supports == current.structural_supports
        and previous.impulse_origin == current.impulse_origin
        and previous.impulse_peak == current.impulse_peak
        and previous.impulse_advance_percent == current.impulse_advance_percent
    )


def _stamp_assessment(assessment: SupportAssessment, assessed_at: datetime) -> SupportAssessment:
    return SupportAssessment.model_validate(
        {
            **assessment.model_dump(mode="python"),
            "data_as_of": assessment.occurred_at,
            "assessed_at": assessed_at,
        }
    )


async def run_support_confirmation_process(
    *, ready_path: Path | None = None, once: bool = False, symbol: str | None = None
) -> dict[str, object] | None:
    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    provider = PostgresUniverseClient(database)
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    try:
        holdings = await load_support_holdings(provider)
        requested = symbol.strip().upper() if symbol is not None else None
        if requested is not None and requested not in holdings.symbols:
            return {
                "service": "support-confirmation-v0",
                "mode": "ACTIVE",
                "requested_symbol": requested,
                "eligible": False,
                "reason": "positive_holding_required",
                "assessments_published": 0,
            }
        selected_symbols = (requested,) if requested is not None else holdings.symbols
        bus = await connect_nats(settings)
        runtime = SupportConfirmationRuntime(
            engine=assembly.build_support_confirmation(), publisher=bus
        )
        restore_subscription = await bus.subscribe(
            "marketbot.v1.support-confirmation.assessment.>",
            runtime.restore_assessment,
            options=SubscriptionOptions(
                replay_latest_per_subject=True,
                ack_wait_seconds=60,
            ),
        )
        subscriptions.append(restore_subscription)
        await bus.wait_until_caught_up(restore_subscription, timeout_seconds=60)
        bars = await load_market_history(
            settings,
            database,
            engine_id="support-confirmation-v0",
            symbols=selected_symbols,
            requirements=SUPPORT_HISTORY_REQUESTS,
            as_of=SystemClock().now(),
        )
        published = await runtime.bootstrap(bars, symbols=selected_symbols)
        summary: dict[str, object] = {
            **universe_health_details("support-confirmation"),
            "service": "support-confirmation-v0",
            "engine_version": assembly.spec(EngineSlot.SUPPORT_CONFIRMATION).implementation,
            "engine_strategy_version": assembly.spec(
                EngineSlot.SUPPORT_CONFIRMATION
            ).strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "mode": "ACTIVE",
            "universe": "positive-holdings-only",
            "universe_source": holdings.source,
            "symbols": list(selected_symbols),
            "assessments_published": published,
            "persistence": "nats-jetstream-7d",
            "feeds_patreon_caps": False,
        }
        if once:
            return summary
        for index, subject in enumerate(
            (
                "marketbot.v1.market.bar.1Day.>",
                "marketbot.v1.market.bar.1Week.>",
            ),
            start=1,
        ):
            subscriptions.append(
                await bus.subscribe(
                    subject,
                    runtime.handle_market,
                    options=SubscriptionOptions(
                        durable_name=f"marketbot-support-confirmation-v0-{index}",
                        replay_all=False,
                        ack_wait_seconds=60,
                    ),
                )
            )
        if ready_path is not None:
            write_ready(ready_path, summary)
        await asyncio.Event().wait()
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        if bus is not None:
            await bus.close()
        await database.dispose()
    return None
