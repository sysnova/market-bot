"""Independent Alpaca news panel for the local investment universe."""

from __future__ import annotations

import asyncio
import html
import re
import sys
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import shorten
from typing import TextIO
from zoneinfo import ZoneInfo

from app.alpaca_market_data import (
    AlpacaMarketDataError,
    AlpacaNewsArticle,
    AlpacaRestClient,
)
from app.alpaca_market_data.transports import HttpxTransport
from app.common.settings import AppSettings, Environment
from app.persistence.database import create_database_engine

from .distributed_composition import write_ready
from .postgres_universe import PostgresUniverseClient, PostgresUniverseError

_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")
_HOLDING_COLOR = "\033[1;33m"
_ERROR_COLOR = "\033[1;31m"
_RESET_COLOR = "\033[0m"
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True, slots=True)
class NewsUniverse:
    """Current symbols and membership used to classify news."""

    symbols: tuple[str, ...]
    portfolio_symbols: frozenset[str]
    holding_symbols: frozenset[str]


async def run_news_monitor(
    *,
    ready_path: Path | None = None,
    refresh_interval: timedelta = timedelta(hours=1),
    lookback: timedelta = timedelta(hours=24),
    history: int = 100,
    batch_size: int = 50,
    stream: TextIO | None = None,
    color: bool = True,
) -> None:
    """Poll Alpaca news and append each unseen article to a dedicated terminal."""

    if refresh_interval < timedelta(seconds=5):
        raise ValueError("news refresh interval must be at least 5 seconds")
    if lookback <= timedelta(0):
        raise ValueError("news lookback must be positive")
    if not 1 <= history <= 1_000:
        raise ValueError("news history must be between 1 and 1000")
    if not 1 <= batch_size <= 100:
        raise ValueError("news batch size must be between 1 and 100")

    settings = AppSettings()
    if not settings.alpaca_configured:
        raise ValueError("Alpaca market-data credentials are not configured")
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None

    output = stream or sys.stdout
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    universe_provider = PostgresUniverseClient(database)
    client = AlpacaRestClient(
        api_key_id=settings.alpaca_api_key_id.get_secret_value(),
        api_secret_key=settings.alpaca_api_secret_key.get_secret_value(),
        base_url=str(settings.alpaca_data_base_url),
        feed=settings.alpaca_data_feed,
        adjustment=settings.alpaca_adjustment,
        transport=HttpxTransport(),
        max_pages=20,
    )
    seen_ids: set[int] = set()
    seen_order: deque[int] = deque()
    previous_universe: NewsUniverse | None = None
    query_start = datetime.now(UTC) - lookback
    initial_load = True
    ready_written = False

    try:
        while True:
            cycle_started = datetime.now(UTC)
            try:
                universe = await _load_news_universe(universe_provider)
                if universe != previous_universe:
                    _print_universe_header(universe, refresh_interval, output=output)
                    previous_universe = universe
                articles = await _fetch_news_batches(
                    client,
                    universe.symbols,
                    start=query_start,
                    batch_size=batch_size,
                )
                unseen = [article for article in articles if article.article_id not in seen_ids]
                if initial_load:
                    unseen = sorted(unseen, key=_article_order, reverse=True)[:history]
                for article in sorted(unseen, key=_article_order):
                    print(
                        format_news_article(article, universe, color=color),
                        file=output,
                        flush=True,
                    )
                    _remember(article.article_id, seen_ids, seen_order)
                if initial_load and not unseen:
                    print(
                        "Sin noticias recientes para el universo configurado.",
                        file=output,
                        flush=True,
                    )
                if ready_path is not None and not ready_written:
                    write_ready(
                        ready_path,
                        {
                            "service": "alpaca-news-monitor",
                            "source": "alpaca-news-rest",
                            "symbols": len(universe.symbols),
                            "portfolio_symbols": len(universe.portfolio_symbols),
                            "holding_symbols": len(universe.holding_symbols),
                            "refresh_seconds": int(refresh_interval.total_seconds()),
                        },
                    )
                    ready_written = True
                initial_load = False
                query_start = cycle_started - timedelta(minutes=2)
            except (AlpacaMarketDataError, PostgresUniverseError, OSError) as error:
                _print_error(error, output=output, color=color)
            await asyncio.sleep(refresh_interval.total_seconds())
    finally:
        await client.close()
        await database.dispose()


async def _load_news_universe(provider: PostgresUniverseClient) -> NewsUniverse:
    universe, allocations, holdings = await asyncio.gather(
        provider.get_universe(),
        provider.get_portfolio_allocations(),
        provider.get_holdings(),
    )
    portfolio_symbols = frozenset(allocation.symbol for allocation in allocations)
    holding_symbols = frozenset(holdings.symbols)
    symbols = tuple(
        dict.fromkeys(
            (*universe.symbols, *sorted(portfolio_symbols), *sorted(holding_symbols))
        )
    )
    if not symbols:
        raise PostgresUniverseError("Local PostgreSQL returned an empty news universe")
    return NewsUniverse(
        symbols=symbols,
        portfolio_symbols=portfolio_symbols,
        holding_symbols=holding_symbols,
    )


async def _fetch_news_batches(
    client: AlpacaRestClient,
    symbols: tuple[str, ...],
    *,
    start: datetime,
    batch_size: int,
) -> tuple[AlpacaNewsArticle, ...]:
    articles: dict[int, AlpacaNewsArticle] = {}
    for batch in _batches(symbols, batch_size):
        for article in await client.fetch_news(batch, start=start):
            articles[article.article_id] = article
    return tuple(articles.values())


def format_news_article(
    article: AlpacaNewsArticle,
    universe: NewsUniverse,
    *,
    color: bool,
) -> str:
    """Render one article, highlighting it only when a held ticker is involved."""

    matched_symbols = tuple(symbol for symbol in article.symbols if symbol in universe.symbols)
    held = tuple(symbol for symbol in matched_symbols if symbol in universe.holding_symbols)
    portfolio = tuple(
        symbol for symbol in matched_symbols if symbol in universe.portfolio_symbols
    )
    if held:
        classification = "★ TENENCIA"
    elif portfolio:
        classification = "PORTFOLIO"
    else:
        classification = "WATCHLIST"
    displayed_symbols = matched_symbols or article.symbols
    occurred_at = article.created_at.astimezone(_ARGENTINA).strftime("%d-%m %H:%M AR")
    source = _clean_text(article.source or article.author or "Alpaca")
    heading = (
        f"[{occurred_at}] {classification} | {','.join(displayed_symbols)} | {source}"
    )
    lines = [
        heading,
        f"  {_short_text(article.headline, width=180)}",
    ]
    summary = _short_text(article.summary, width=360)
    if summary:
        lines.append(f"  {summary}")
    url = _clean_text(article.url)
    if url:
        lines.append(f"  {url}")
    rendered = "\n".join(lines)
    if color and held:
        return f"{_HOLDING_COLOR}{rendered}{_RESET_COLOR}"
    return rendered


def _print_universe_header(
    universe: NewsUniverse,
    refresh_interval: timedelta,
    *,
    output: TextIO,
) -> None:
    print(
        "\nALPACA NEWS — "
        f"{len(universe.symbols)} símbolos | "
        f"portfolio {len(universe.portfolio_symbols)} | "
        f"tenencias {len(universe.holding_symbols)} | "
        f"actualiza cada {int(refresh_interval.total_seconds())}s",
        file=output,
        flush=True,
    )
    print(
        "Las noticias de tenencias aparecen como ★ TENENCIA en amarillo.",
        file=output,
        flush=True,
    )


def _print_error(error: Exception, *, output: TextIO, color: bool) -> None:
    message = (
        "Noticias temporalmente no disponibles; se reintentará "
        f"({type(error).__name__})."
    )
    if color:
        message = f"{_ERROR_COLOR}{message}{_RESET_COLOR}"
    print(message, file=output, flush=True)


def _remember(
    article_id: int,
    seen_ids: set[int],
    seen_order: deque[int],
    *,
    capacity: int = 5_000,
) -> None:
    if article_id in seen_ids:
        return
    if len(seen_order) >= capacity:
        seen_ids.remove(seen_order.popleft())
    seen_order.append(article_id)
    seen_ids.add(article_id)


def _batches(symbols: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for offset in range(0, len(symbols), size):
        yield symbols[offset : offset + size]


def _article_order(article: AlpacaNewsArticle) -> tuple[datetime, int]:
    return article.created_at, article.article_id


def _short_text(value: str, *, width: int) -> str:
    cleaned = _clean_text(value)
    return shorten(cleaned, width=width, placeholder="…") if cleaned else ""


def _clean_text(value: str) -> str:
    decoded = html.unescape(value)
    without_controls = _CONTROL_CHARACTERS.sub(" ", decoded)
    return " ".join(without_controls.split())
