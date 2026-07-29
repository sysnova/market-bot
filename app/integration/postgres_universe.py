"""Read the shared Stock Analyzer universe directly from PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresUniverseError(RuntimeError):
    """Raised when the local shared universe cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    symbols: tuple[str, ...]
    source: str
    watchlist_updated_at: str | None = None
    holdings_updated_at: str | None = None


class PostgresUniverseClient:
    """Load Stock Analyzer's active watchlist and positive holdings locally."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        customer_slug: str = "stock-analyzer",
        watchlist_code: str = "default",
    ) -> None:
        self._engine = engine
        self._customer_slug = customer_slug
        self._watchlist_code = watchlist_code

    async def get_universe(self) -> UniverseSnapshot:
        try:
            async with self._engine.connect() as connection:
                watchlist = (
                    await connection.execute(
                        text(
                            """
                            select ws.symbol, w.updated_at
                            from stock.watchlist w
                            join stock.customer c on c.id = w.customer_id
                            join stock.watchlist_symbol ws on ws.watchlist_id = w.id
                            where c.slug = :customer_slug
                              and c.status = 'active'
                              and w.code = :watchlist_code
                              and w.status = 'active'
                              and ws.status = 'active'
                            order by ws.sort_order asc, ws.symbol asc
                            """
                        ),
                        {
                            "customer_slug": self._customer_slug,
                            "watchlist_code": self._watchlist_code,
                        },
                    )
                ).all()
                holdings = (
                    await connection.execute(
                        text(
                            """
                            select h.symbol, h.updated_at
                            from stock.customer_holding h
                            join stock.customer c on c.id = h.customer_id
                            where c.slug = :customer_slug
                              and c.status = 'active'
                              and h.status = 'active'
                              and h.quantity > 0
                            order by h.symbol asc
                            """
                        ),
                        {"customer_slug": self._customer_slug},
                    )
                ).all()
        except SQLAlchemyError as error:
            raise PostgresUniverseError("Local PostgreSQL universe query failed") from error

        symbols = _normalize_symbols(
            (
                *(str(row.symbol) for row in watchlist),
                *(str(row.symbol) for row in holdings),
            )
        )
        if not symbols:
            raise PostgresUniverseError("Local PostgreSQL returned an empty market universe")
        return UniverseSnapshot(
            symbols=symbols,
            source="postgresql-local",
            watchlist_updated_at=_latest_timestamp(row.updated_at for row in watchlist),
            holdings_updated_at=_latest_timestamp(row.updated_at for row in holdings),
        )


def fallback_universe(symbols: Sequence[str], *, source: str = "env-fallback") -> UniverseSnapshot:
    normalized = _normalize_symbols(symbols)
    if not normalized:
        raise ValueError("at least one fallback market symbol is required")
    return UniverseSnapshot(symbols=normalized, source=source)


def _latest_timestamp(values: Iterable[object]) -> str | None:
    timestamps = tuple(value for value in values if isinstance(value, datetime))
    return max(timestamps).isoformat() if timestamps else None


def _normalize_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
