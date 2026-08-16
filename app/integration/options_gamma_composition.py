"""Active Options Gamma composition over Alpaca, PostgreSQL universe and NATS."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol, cast

from app.alpaca_market_data import AlpacaRestClient
from app.alpaca_market_data.transports import HttpxTransport
from app.common.clock import SystemClock
from app.common.logging import configure_logging, get_logger
from app.common.settings import AppSettings, Environment
from app.contracts import (
    ANALYSIS_RESULT_EVENT,
    OPTIONS_GAMMA_ASSESSMENT_EVENT,
    UNIVERSE_CHANGED_EVENT,
    EventEnvelope,
    GammaAssessment,
    Subscription,
    SubscriptionOptions,
    UniverseChanged,
    analysis_result_subject,
    options_gamma_assessment_subject,
)
from app.event_bus import NatsJetStreamEventBus
from app.options_gamma_engine import (
    OptionContractSnapshot,
    OptionsGammaContext,
    gamma_analysis_from_assessment,
)
from app.persistence import create_database_engine

from .distributed_composition import connect_nats, write_ready
from .engine_assembly import EngineSlot, MarketBotAssembly
from .options_gamma_alpaca import (
    AlpacaOptionContractsClient,
    AlpacaOptionsDataClient,
    OptionOpenInterest,
)
from .postgres_universe import PostgresUniverseClient, fallback_universe
from .universe_policy import universe_health_details


class StockSnapshotProvider(Protocol):
    async def fetch_snapshots(
        self, symbols: tuple[str, ...]
    ) -> dict[str, Mapping[str, object]]: ...


class OptionChainProvider(Protocol):
    async def fetch_chain(
        self,
        symbol: str,
        *,
        expiration_from: date,
        expiration_to: date,
        strike_from: str,
        strike_to: str,
    ) -> tuple[OptionContractSnapshot, ...]: ...


class OptionOpenInterestProvider(Protocol):
    async def fetch_open_interest(
        self,
        symbol: str,
        *,
        expiration_from: date,
        expiration_to: date,
    ) -> tuple[OptionOpenInterest, ...]: ...


class GammaEnginePort(Protocol):
    def evaluate(self, context: OptionsGammaContext) -> GammaAssessment: ...


class GammaPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


@dataclass(frozen=True, slots=True)
class GammaRefreshSummary:
    symbols_requested: int
    assessments_published: int
    failures: dict[str, str]


class OptionsGammaRuntime:
    """Refresh a replaceable Core universe without coupling failures between symbols."""

    def __init__(
        self,
        *,
        engine: GammaEnginePort,
        stock_provider: StockSnapshotProvider,
        option_provider: OptionChainProvider,
        open_interest_provider: OptionOpenInterestProvider,
        publisher: GammaPublisher,
        days_forward: int,
        strike_range_percent: Decimal,
        concurrency: int,
    ) -> None:
        self._engine = engine
        self._stock_provider = stock_provider
        self._option_provider = option_provider
        self._open_interest_provider = open_interest_provider
        self._publisher = publisher
        self._days_forward = days_forward
        self._strike_range_percent = strike_range_percent
        self._semaphore = asyncio.Semaphore(concurrency)
        self._symbols: tuple[str, ...] = ()
        self._universe_changed = asyncio.Event()

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def universe_changed(self) -> asyncio.Event:
        return self._universe_changed

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        normalized = tuple(
            dict.fromkeys(item.strip().upper() for item in symbols if item.strip())
        )
        if not normalized:
            raise ValueError("Options Gamma requires at least one symbol")
        if normalized != self._symbols:
            self._symbols = normalized
            self._universe_changed.set()

    async def handle_universe(self, envelope: EventEnvelope) -> None:
        if envelope.event_type != UNIVERSE_CHANGED_EVENT:
            return
        change = (
            envelope.payload
            if isinstance(envelope.payload, UniverseChanged)
            else UniverseChanged.model_validate(envelope.payload, strict=False)
        )
        self.set_symbols(change.symbols)

    async def refresh(self, *, now: datetime) -> GammaRefreshSummary:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("Options Gamma refresh time must be UTC")
        symbols = self._symbols
        results = await asyncio.gather(
            *(self._analyze(symbol, now=now) for symbol in symbols),
            return_exceptions=True,
        )
        failures: dict[str, str] = {}
        published = 0
        for symbol, result in zip(symbols, results, strict=True):
            if isinstance(result, BaseException):
                failures[symbol] = type(result).__name__
            else:
                published += 1
        return GammaRefreshSummary(
            symbols_requested=len(symbols),
            assessments_published=published,
            failures=failures,
        )

    async def _analyze(self, symbol: str, *, now: datetime) -> None:
        async with self._semaphore:
            snapshots = await self._stock_provider.fetch_snapshots((symbol,))
            spot, spot_as_of = _spot_snapshot(snapshots.get(symbol), fallback_at=now)
            expiration_from = now.date()
            expiration_to = expiration_from + timedelta(days=self._days_forward)
            width = self._strike_range_percent / Decimal("100")
            strike_from = max(Decimal("0.01"), spot * (Decimal("1") - width))
            strike_to = spot * (Decimal("1") + width)
            chain_result, open_interest_result = await asyncio.gather(
                self._option_provider.fetch_chain(
                    symbol,
                    expiration_from=expiration_from,
                    expiration_to=expiration_to,
                    strike_from=_decimal_text(strike_from),
                    strike_to=_decimal_text(strike_to),
                ),
                self._open_interest_provider.fetch_open_interest(
                    symbol,
                    expiration_from=expiration_from,
                    expiration_to=expiration_to,
                ),
                return_exceptions=True,
            )
            if isinstance(chain_result, BaseException):
                raise chain_result
            provider_warnings: tuple[str, ...] = ()
            if isinstance(open_interest_result, BaseException):
                open_interest = ()
                provider_warnings = ("open_interest_source_unavailable",)
            else:
                open_interest = open_interest_result
            contracts = _merge_open_interest(chain_result, open_interest)
            context = OptionsGammaContext(
                symbol=symbol,
                spot_price=spot,
                spot_as_of=spot_as_of,
                generated_at=now,
                expiration_from=expiration_from,
                expiration_to=expiration_to,
                contracts=contracts,
                provider_warnings=provider_warnings,
            )
            assessment = self._engine.evaluate(context)
            await self._publisher.publish(
                options_gamma_assessment_subject(symbol),
                EventEnvelope(
                    event_type=OPTIONS_GAMMA_ASSESSMENT_EVENT,
                    occurred_at=assessment.generated_at,
                    source="options-gamma-v1",
                    subject=symbol,
                    payload=assessment,
                ),
            )
            analysis = gamma_analysis_from_assessment(assessment)
            await self._publisher.publish(
                analysis_result_subject(analysis.horizon, symbol),
                EventEnvelope(
                    event_type=ANALYSIS_RESULT_EVENT,
                    occurred_at=analysis.as_of,
                    source="options-gamma-v1",
                    subject=symbol,
                    payload=analysis,
                ),
            )


async def run_options_gamma_process(
    *,
    ready_path: Path | None = None,
    once: bool = False,
    symbols: str | None = None,
) -> dict[str, object] | None:
    settings = AppSettings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger = get_logger("options-gamma")
    if not settings.alpaca_configured:
        raise RuntimeError("Options Gamma requires Alpaca credentials")
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None
    api_key = settings.alpaca_api_key_id.get_secret_value()
    api_secret = settings.alpaca_api_secret_key.get_secret_value()
    assembly = MarketBotAssembly.from_settings(settings)
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    provider = PostgresUniverseClient(database)
    bus: NatsJetStreamEventBus | None = None
    subscriptions: list[Subscription] = []
    stock_client = AlpacaRestClient(
        api_key_id=api_key,
        api_secret_key=api_secret,
        base_url=str(settings.alpaca_data_base_url),
        feed=settings.alpaca_data_feed,
        adjustment=settings.alpaca_adjustment,
        transport=HttpxTransport(),
    )
    option_client = AlpacaOptionsDataClient(
        api_key_id=api_key,
        api_secret_key=api_secret,
        base_url=str(settings.alpaca_options_data_base_url),
        feed=settings.alpaca_options_feed,
        transport=HttpxTransport(),
    )
    open_interest_client = AlpacaOptionContractsClient(
        api_key_id=api_key,
        api_secret_key=api_secret,
        base_url=str(settings.alpaca_options_contracts_base_url),
        transport=HttpxTransport(),
    )
    try:
        requested = tuple(
            dict.fromkeys(
                item.strip().upper() for item in (symbols or "").split(",") if item.strip()
            )
        )
        universe = (
            fallback_universe(requested, source="operator-symbols")
            if requested
            else await provider.get_universe()
        )
        bus = await connect_nats(settings)
        engine = cast("GammaEnginePort", assembly.build(EngineSlot.OPTIONS_GAMMA))
        runtime = OptionsGammaRuntime(
            engine=engine,
            stock_provider=stock_client,
            option_provider=option_client,
            open_interest_provider=open_interest_client,
            publisher=bus,
            days_forward=settings.options_gamma_days_forward,
            strike_range_percent=settings.options_gamma_strike_range_percent,
            concurrency=settings.options_gamma_concurrency,
        )
        runtime.set_symbols(universe.symbols)
        runtime.universe_changed.clear()
        if not requested:
            subscriptions.append(
                await bus.subscribe(
                    "marketbot.v1.universe.changed.core",
                    runtime.handle_universe,
                    options=SubscriptionOptions(
                        durable_name="marketbot-options-gamma-v1-universe",
                        replay_all=False,
                        ack_wait_seconds=60,
                    ),
                )
            )
        first = await runtime.refresh(now=SystemClock().now())
        details: dict[str, object] = {
            **universe_health_details("options-gamma"),
            "service": "options-gamma-v1",
            "engine_version": assembly.spec(EngineSlot.OPTIONS_GAMMA).implementation,
            "engine_strategy_version": assembly.spec(EngineSlot.OPTIONS_GAMMA).strategy.version,
            "marketbot_definition_version": assembly.definition.version,
            "mode": "ACTIVE",
            "universe": "watchlist-plus-positive-holdings",
            "universe_source": universe.source,
            "symbols": list(runtime.symbols),
            "refresh_seconds": settings.options_gamma_refresh_seconds,
            "assessments_published": first.assessments_published,
            "failures": first.failures,
            "persistence": "nats-jetstream-7d",
            "execution_enabled": False,
        }
        if once:
            return details
        if ready_path is not None:
            write_ready(ready_path, details)
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    runtime.universe_changed.wait(),
                    timeout=settings.options_gamma_refresh_seconds,
                )
            runtime.universe_changed.clear()
            summary = await runtime.refresh(now=SystemClock().now())
            if summary.failures:
                await logger.awarning(
                    "options_gamma_partial_refresh",
                    failures=summary.failures,
                    published=summary.assessments_published,
                )
    finally:
        for subscription in subscriptions:
            await subscription.unsubscribe()
        await stock_client.close()
        await option_client.close()
        await open_interest_client.close()
        if bus is not None:
            await bus.close()
        await database.dispose()
    return None


def _merge_open_interest(
    snapshots: tuple[OptionContractSnapshot, ...],
    open_interest: tuple[OptionOpenInterest, ...],
) -> tuple[OptionContractSnapshot, ...]:
    by_symbol = {item.symbol.strip().upper(): item for item in open_interest}
    return tuple(
        item
        if (metadata := by_symbol.get(item.symbol.strip().upper())) is None
        else replace(
            item,
            open_interest=(
                metadata.open_interest
                if metadata.open_interest is not None
                else item.open_interest
            ),
            open_interest_date=(
                metadata.open_interest_date
                if metadata.open_interest_date is not None
                else item.open_interest_date
            ),
        )
        for item in snapshots
    )


def _spot_snapshot(
    raw: Mapping[str, object] | None, *, fallback_at: datetime
) -> tuple[Decimal, datetime]:
    if raw is None:
        raise ValueError("stock snapshot is missing")
    for key in ("latestTrade", "minuteBar", "dailyBar", "prevDailyBar"):
        item = raw.get(key)
        if not isinstance(item, Mapping):
            continue
        snapshot = cast("Mapping[str, object]", item)
        price = _decimal(
            snapshot.get("p") if key == "latestTrade" else snapshot.get("c")
        )
        if price is None or price <= 0:
            continue
        timestamp = _datetime(snapshot.get("t")) or fallback_at
        return price, timestamp
    raise ValueError("stock snapshot has no positive price")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")
