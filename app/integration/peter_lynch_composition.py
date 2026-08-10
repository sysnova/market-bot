"""Composition root for the one-shot manual Peter Lynch watchlist screen."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

import httpx

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.dilution_sec_engine import (
    SecAdapterError,
    SecEdgarConfig,
    SecTickerNotFoundError,
    SecTickerResolver,
)
from app.persistence import create_database_engine
from app.peter_lynch_engine import PeterLynchEngine, PeterLynchEvaluation, PeterLynchSnapshot

from .distributed_composition import build_rest
from .engine_assembly import EngineSlot, MarketBotAssembly
from .peter_lynch_sec_adapter import PeterLynchSecAdapter, SecPeterLynchFacts
from .peter_lynch_store import PeterLynchWorklist, PostgresPeterLynchStore
from .universe_policy import universe_health_details

ProgressReporter = Callable[[str], None]


def _ignore_progress(_message: str) -> None:
    """Default reporter for library callers that do not need console output."""


class WatchlistStore(Protocol):
    async def load_worklist(
        self,
        *,
        as_of: date,
        ttl_days: int,
        engine_version: str,
        policy_version: str,
    ) -> PeterLynchWorklist: ...

    async def save(self, evaluations: tuple[PeterLynchEvaluation, ...]) -> int: ...


class SnapshotPriceProvider(Protocol):
    async def fetch_snapshots(
        self, symbols: tuple[str, ...]
    ) -> dict[str, Mapping[str, object]]: ...


class TickerResolver(Protocol):
    async def resolve(self, symbol: str) -> str: ...


class SecFactsProvider(Protocol):
    async def load(
        self, *, cik: str | int, symbol: str, as_of: date
    ) -> SecPeterLynchFacts: ...


@dataclass(slots=True)
class PeterLynchRunService:
    """Coordinate one bounded run while isolating transient failures by symbol."""

    store: WatchlistStore
    prices: SnapshotPriceProvider
    ticker_resolver: TickerResolver
    sec: SecFactsProvider
    calculator: PeterLynchEngine
    batch_size: int = 20
    analysis_ttl_days: int = 1
    progress: ProgressReporter = field(default=_ignore_progress, repr=False)

    async def run(self, *, now: datetime) -> dict[str, object]:
        if now.tzinfo is None:
            raise ValueError("Peter Lynch run time must be timezone-aware")
        if self.batch_size < 1:
            raise ValueError("Peter Lynch batch size must be positive")
        as_of = now.astimezone(UTC).date()
        if self.analysis_ttl_days < 1:
            raise ValueError("Peter Lynch analysis TTL must be positive")
        worklist = await self.store.load_worklist(
            as_of=as_of,
            ttl_days=self.analysis_ttl_days,
            engine_version=self.calculator.ENGINE_VERSION,
            policy_version=self.calculator.POLICY_VERSION,
        )
        symbols = worklist.symbols
        self.progress(
            f"Watchlist: {worklist.total} símbolos activos; "
            f"{worklist.skipped_current} análisis vigentes omitidos; "
            f"{len(symbols)} pendientes."
        )
        snapshots: dict[str, Mapping[str, object]] = {}
        failed_price_symbols: set[str] = set()
        for index in range(0, len(symbols), self.batch_size):
            batch = symbols[index : index + self.batch_size]
            first = index + 1
            last = index + len(batch)
            self.progress(
                f"Alpaca: descargando precios {first}-{last} de {len(symbols)}."
            )
            try:
                snapshots.update(await self.prices.fetch_snapshots(batch))
                self.progress(f"Alpaca: precios {first}-{last} recibidos.")
            except Exception:
                failed_price_symbols.update(batch)
                self.progress(
                    f"Alpaca: error en precios {first}-{last}; "
                    "se preservan las etiquetas existentes."
                )

        evaluations: list[PeterLynchEvaluation] = []
        selected = 0
        discarded = 0
        unsupported = 0
        errors = len(failed_price_symbols)
        evaluated = 0
        for position, symbol in enumerate(symbols, start=1):
            if symbol in failed_price_symbols:
                continue
            price, price_as_of = _snapshot_price(snapshots.get(symbol, {}), as_of=as_of)
            self.progress(f"[{position}/{len(symbols)}] {symbol}: resolviendo CIK SEC.")
            try:
                cik = await self.ticker_resolver.resolve(symbol)
            except SecTickerNotFoundError:
                unsupported += 1
                self.progress(
                    f"[{position}/{len(symbols)}] {symbol}: no soportado por SEC."
                )
                evaluations.append(
                    self.calculator.evaluate(
                        _unsupported_snapshot(
                            symbol=symbol,
                            as_of=as_of,
                            price=price,
                            price_as_of=price_as_of,
                        )
                    )
                )
                continue
            except SecAdapterError:
                errors += 1
                self.progress(
                    f"[{position}/{len(symbols)}] {symbol}: error SEC al resolver CIK; "
                    "se preserva la etiqueta existente."
                )
                continue
            self.progress(
                f"[{position}/{len(symbols)}] {symbol}: consultando fundamentales SEC."
            )
            try:
                facts = await self.sec.load(cik=cik, symbol=symbol, as_of=as_of)
            except SecAdapterError:
                errors += 1
                self.progress(
                    f"[{position}/{len(symbols)}] {symbol}: error SEC; "
                    "se preserva la etiqueta existente."
                )
                continue
            evaluation = self.calculator.evaluate(
                _combined_snapshot(
                    facts,
                    as_of=as_of,
                    price=price,
                    price_as_of=price_as_of,
                )
            )
            evaluations.append(evaluation)
            evaluated += 1
            if evaluation.eligible:
                selected += 1
                outcome = "seleccionado"
            else:
                discarded += 1
                outcome = "descartado"
            self.progress(
                f"[{position}/{len(symbols)}] {symbol}: {outcome} "
                f"{evaluation.passed_count}/{evaluation.required_count} "
                f"({evaluation.category.value})."
            )
        if evaluations:
            self.progress(f"Persistencia: guardando {len(evaluations)} evaluaciones.")
            saved = await self.store.save(tuple(evaluations))
        else:
            saved = 0
        self.progress(f"Persistencia: {saved} evaluaciones actualizadas.")
        return {
            "service": "peter-lynch-v1",
            "analysis_ttl_days": self.analysis_ttl_days,
            "watchlist_total": worklist.total,
            "pending": len(symbols),
            "evaluated": evaluated,
            "selected": selected,
            "discarded": discarded,
            "unsupported": unsupported,
            "errors": errors,
            "saved": saved,
            "skipped_current": worklist.skipped_current,
        }


async def run_peter_lynch_once(
    *,
    progress: ProgressReporter | None = None,
    analysis_ttl_days: int | None = None,
) -> dict[str, object]:
    """Build production adapters, evaluate the active watchlist once, and exit."""

    report = progress or _ignore_progress
    report("Inicializando configuración y conexiones.")
    settings = AppSettings()
    resolved_ttl_days = (
        settings.peter_lynch_analysis_ttl_days
        if analysis_ttl_days is None
        else analysis_ttl_days
    )
    if not 1 <= resolved_ttl_days <= 365:
        raise ValueError("Peter Lynch analysis TTL must be between 1 and 365 days")
    assembly = MarketBotAssembly.from_settings(settings)
    if not settings.alpaca_configured:
        raise ValueError("Alpaca market-data credentials are not configured")
    if not settings.sec_configured or settings.sec_user_agent is None:
        raise ValueError("MARKETBOT_SEC_USER_AGENT must include a contact email")
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    rest = build_rest(settings)
    client = httpx.AsyncClient()
    sec_config = SecEdgarConfig(user_agent=settings.sec_user_agent)
    resolver = SecTickerResolver(sec_config, client=client)
    sec = PeterLynchSecAdapter(
        user_agent=settings.sec_user_agent,
        client=client,
        progress=report,
    )
    try:
        result = await PeterLynchRunService(
            store=PostgresPeterLynchStore(database),
            prices=rest,
            ticker_resolver=resolver,
            sec=sec,
            batch_size=settings.alpaca_rest_batch_size,
            analysis_ttl_days=resolved_ttl_days,
            calculator=assembly.build_peter_lynch(),
            progress=report,
        ).run(now=SystemClock().now())
        spec = assembly.spec(EngineSlot.PETER_LYNCH)
        return {
            **result,
            **universe_health_details("peter-lynch"),
            "marketbot_definition_version": assembly.definition.version,
            "engine_implementation": spec.implementation,
            "engine_strategy_version": spec.strategy.version,
        }
    finally:
        await rest.close()
        await client.aclose()
        await database.dispose()


def _combined_snapshot(
    facts: SecPeterLynchFacts,
    *,
    as_of: date,
    price: Decimal | None,
    price_as_of: date | None,
) -> PeterLynchSnapshot:
    return PeterLynchSnapshot(
        symbol=facts.symbol,
        as_of=as_of,
        price=price,
        price_as_of=price_as_of,
        ttm_eps=facts.ttm_eps,
        prior_ttm_eps=facts.prior_ttm_eps,
        annual_eps=facts.annual_eps,
        debt=facts.debt,
        equity=facts.equity,
        goodwill=facts.goodwill,
        intangibles_ex_goodwill=facts.intangibles_ex_goodwill,
        shares_outstanding=facts.shares_outstanding,
        sic=facts.sic,
        insider_open_market_purchase_count=facts.insider_open_market_purchase_count,
        fundamentals_as_of=facts.fundamentals_as_of,
        latest_insider_purchase_at=facts.latest_insider_purchase_at,
    )


def _unsupported_snapshot(
    *, symbol: str, as_of: date, price: Decimal | None, price_as_of: date | None
) -> PeterLynchSnapshot:
    return PeterLynchSnapshot(
        symbol=symbol,
        as_of=as_of,
        price=price,
        price_as_of=price_as_of,
        ttm_eps=None,
        prior_ttm_eps=None,
        annual_eps=(),
        debt=None,
        equity=None,
        goodwill=None,
        intangibles_ex_goodwill=None,
        shares_outstanding=None,
        sic=None,
        insider_open_market_purchase_count=None,
        fundamentals_as_of=None,
        latest_insider_purchase_at=None,
    )


def _snapshot_price(
    snapshot: Mapping[str, object], *, as_of: date
) -> tuple[Decimal | None, date | None]:
    for snapshot_field, price_field in (("latestTrade", "p"), ("dailyBar", "c")):
        raw = snapshot.get(snapshot_field)
        if not isinstance(raw, Mapping):
            continue
        typed_raw = cast("Mapping[str, object]", raw)
        price = _decimal(typed_raw.get(price_field))
        observed_at = _timestamp_date(typed_raw.get("t"))
        if (
            price is not None
            and price > 0
            and observed_at is not None
            and 0 <= (as_of - observed_at).days <= 5
        ):
            return price, observed_at
    return None, None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _timestamp_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None
