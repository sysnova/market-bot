"""Composition root for the independent bounded daily SEC bot."""

from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from structlog.typing import FilteringBoundLogger

from app.alert_engine import AlertDispatcher, AlertEngine, ConsoleAlertSink, NdjsonAlertSink
from app.common.clock import SystemClock
from app.common.logging import configure_logging, get_logger
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    AnalysisResult,
    EventEnvelope,
    analysis_result_subject,
)
from app.dilution_sec_engine import (
    SecEdgarAdapter,
    SecEdgarConfig,
    SecTickerResolver,
)
from app.event_bus import InMemoryEventBus, NatsJetStreamEventBus
from app.persistence import create_database_engine

from .alert_publisher import AlertEventPublisher
from .engine_assembly import EngineSlot, MarketBotAssembly
from .event_fanout import EventFanoutPublisher, EventPublisher
from .postgres_universe import (
    PostgresUniverseClient,
    fallback_universe,
)
from .sec_refresher import SecAnalysisRefresher

_DILUTION_FORMS = (
    "424B3",
    "424B5",
    "FWP",
    "SUPPL",
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
)
_SEC_TIME_ZONE = ZoneInfo("America/New_York")


class _SecAlertConsumer:
    def __init__(
        self,
        *,
        publisher: EventPublisher,
        alert_engine: AlertEngine,
        dispatcher: AlertDispatcher,
        clock: SystemClock,
    ) -> None:
        self._publisher = publisher
        self._alert_engine = alert_engine
        self._dispatcher = dispatcher
        self._clock = clock

    async def ingest_analysis(self, result: AnalysisResult) -> None:
        await self._publisher.publish(
            analysis_result_subject(result.horizon, result.symbol),
            EventEnvelope(
                event_type=ANALYSIS_RESULT_EVENT,
                occurred_at=result.as_of,
                source=result.engine_id,
                subject=result.symbol,
                payload=result,
            ),
        )
        alert = self._alert_engine.ingest(result, now=self._clock.now())
        if alert is not None:
            await self._dispatcher.dispatch(alert)


async def run_sec_daily_analysis(
    *,
    runtime_root: Path,
    mirror_to_nats: bool,
    bell: bool,
    lookback_days: int | None = None,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Scan only recent dilution-related filings and exit."""

    settings = AppSettings()
    assembly = MarketBotAssembly.from_settings(settings)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger("sec-daily")
    if not settings.sec_enabled or not settings.sec_configured:
        raise ValueError(
            "Daily SEC bot requires MARKETBOT_SEC_ENABLED=true and a contact-bearing "
            "MARKETBOT_SEC_USER_AGENT"
        )
    if settings.sec_user_agent is None:
        raise ValueError("Daily SEC bot requires MARKETBOT_SEC_USER_AGENT")
    resolved_lookback = lookback_days or settings.sec_filing_lookback_days
    if not 1 <= resolved_lookback <= 30:
        raise ValueError("SEC filing lookback must be between 1 and 30 days")

    clock = SystemClock()
    as_of = clock.now().astimezone(_SEC_TIME_ZONE)
    http_client = httpx.AsyncClient()
    local_bus = InMemoryEventBus()
    nats_bus: NatsJetStreamEventBus | None = None
    if mirror_to_nats:
        try:
            nats_bus = await NatsJetStreamEventBus.connect(
                servers=[settings.nats_url.get_secret_value()],
                prefix="marketbot",
                stream="MARKETBOT",
            )
        except Exception as error:
            await logger.awarning(
                "nats_unavailable_sec_analysis_continues",
                error_type=type(error).__name__,
            )

    async def mirror_error(subject: str, error: Exception) -> None:
        await logger.awarning(
            "nats_mirror_failed",
            subject=subject,
            error_type=type(error).__name__,
        )

    publisher = EventFanoutPublisher(
        primary=local_bus,
        mirrors=(nats_bus,) if nats_bus is not None else (),
        on_mirror_error=mirror_error,
    )
    alert_ledger = NdjsonAlertSink(runtime_root / "alerts" / "marketbot-alerts.ndjson")
    dispatcher = AlertDispatcher(
        sinks=(
            ConsoleAlertSink(stream=sys.stdout, bell=bell),
            alert_ledger,
        ),
        publisher=AlertEventPublisher(publisher),
    )
    consumer = _SecAlertConsumer(
        publisher=publisher,
        alert_engine=assembly.build_alert(),
        dispatcher=dispatcher,
        clock=clock,
    )

    universe_database = None
    if symbols:
        universe = fallback_universe(symbols, source="manual-symbols")
    else:
        universe_database = create_database_engine(
            settings.database_url.get_secret_value(),
            require_ssl=settings.environment is Environment.PRODUCTION,
        )
        universe_provider = PostgresUniverseClient(
            universe_database,
        )
        universe = await universe_provider.get_universe()

    sec_config = SecEdgarConfig(
        user_agent=settings.sec_user_agent,
        max_recent_filings=50,
        max_signal_documents=0,
        filing_lookback_days=resolved_lookback,
        included_forms=_DILUTION_FORMS,
        companyfacts_only_with_filings=True,
    )
    refresher = SecAnalysisRefresher(
        resolver=SecTickerResolver(sec_config, client=http_client),
        loader=SecEdgarAdapter(sec_config, client=http_client),
        engine=assembly.build_dilution_sec(),
        runtime=consumer,
        skip_without_filings=True,
        on_error=lambda symbol, error: _log_sec_error(logger, symbol, error),
    )
    try:
        summary = await refresher.refresh(universe.symbols, as_of)
        await local_bus.join()
        result: dict[str, Any] = {
            **asdict(summary),
            "date_from": (as_of.date() - timedelta(days=resolved_lookback - 1)).isoformat(),
            "date_to": as_of.date().isoformat(),
            "forms": list(_DILUTION_FORMS),
            "nats_mirroring": nats_bus is not None,
            "execution_enabled": False,
            "universe_source": universe.source,
            "alert_path": str(alert_ledger.path_for(as_of)),
            "marketbot_definition_version": assembly.definition.version,
            "engine_implementation": assembly.spec(EngineSlot.DILUTION_SEC).implementation,
            "engine_strategy_version": assembly.spec(EngineSlot.DILUTION_SEC).strategy.version,
        }
        await logger.ainfo("sec_daily_complete", **result)
        return result
    finally:
        await http_client.aclose()
        if universe_database is not None:
            await universe_database.dispose()
        if nats_bus is not None:
            await nats_bus.close()
        await local_bus.close()


def _log_sec_error(
    logger: FilteringBoundLogger,
    symbol: str,
    error: Exception,
) -> None:
    logger.warning(
        "sec_daily_symbol_failed",
        symbol=symbol,
        error_type=type(error).__name__,
    )
