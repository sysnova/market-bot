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
from app.common.settings import AppSettings
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    AnalysisResult,
    EventEnvelope,
    analysis_result_subject,
)
from app.dilution_sec_engine import (
    DilutionSecEngine,
    SecEdgarAdapter,
    SecEdgarConfig,
    SecTickerResolver,
)
from app.event_bus import InMemoryEventBus, NatsJetStreamEventBus

from .alert_publisher import AlertEventPublisher
from .event_fanout import EventFanoutPublisher, EventPublisher
from .sec_refresher import SecAnalysisRefresher
from .supabase_universe import (
    SupabaseUniverseClient,
    SupabaseUniverseConfig,
    fallback_universe,
)

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
        alert_engine=AlertEngine(),
        dispatcher=dispatcher,
        clock=clock,
    )

    if symbols:
        universe = fallback_universe(symbols, source="manual-symbols")
    elif settings.supabase_universe_configured:
        if settings.supabase_url is None or settings.supabase_desktop_api_key is None:
            raise RuntimeError("Supabase universe configuration is incomplete")
        universe_provider = SupabaseUniverseClient(
            SupabaseUniverseConfig(
                base_url=str(settings.supabase_url),
                desktop_api_key=settings.supabase_desktop_api_key.get_secret_value(),
                fallback_symbols=settings.alpaca_symbols,
            ),
            client=http_client,
        )
        try:
            universe = await universe_provider.get_universe()
        except Exception as error:
            await logger.awarning(
                "supabase_universe_unavailable_using_fallback",
                error_type=type(error).__name__,
            )
            universe = fallback_universe(settings.alpaca_symbols)
    else:
        universe = fallback_universe(settings.alpaca_symbols)

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
        engine=DilutionSecEngine(),
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
        }
        await logger.ainfo("sec_daily_complete", **result)
        return result
    finally:
        await http_client.aclose()
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
