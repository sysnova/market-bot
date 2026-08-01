"""Read the shared Stock Analyzer universe directly from PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.long_portfolio_engine import PortfolioAllocation


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

    async def get_holdings(self) -> UniverseSnapshot:
        """Load only active positive holdings for portfolio tick monitoring."""

        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(text("""
                    select h.symbol, h.updated_at
                    from stock.customer_holding h
                    join stock.customer c on c.id = h.customer_id
                    where c.slug = :customer_slug and c.status = 'active'
                      and h.status = 'active' and h.quantity > 0
                    order by h.symbol asc
                """), {"customer_slug": self._customer_slug})).all()
        except SQLAlchemyError as error:
            raise PostgresUniverseError("Local PostgreSQL holdings query failed") from error
        return UniverseSnapshot(
            symbols=_normalize_symbols(tuple(str(row.symbol) for row in rows)),
            source="postgresql-local-holdings",
            holdings_updated_at=_latest_timestamp(row.updated_at for row in rows),
        )

    async def get_portfolio_allocations(
        self, *, indicator: str = "PORT_YTD"
    ) -> tuple[PortfolioAllocation, ...]:
        """Load an active tagged portfolio and its target weights from PostgreSQL."""

        try:
            async with self._engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            """
                            select
                              ws.symbol,
                              ws.metadata_json #>> array[
                                'indicatorDetails', :indicator, 'targetWeightPercent'
                              ] as target_weight_percent
                            from stock.watchlist w
                            join stock.customer c on c.id = w.customer_id
                            join stock.watchlist_symbol ws on ws.watchlist_id = w.id
                            where c.slug = :customer_slug
                              and c.status = 'active'
                              and w.code = :watchlist_code
                              and w.status = 'active'
                              and ws.status = 'active'
                              and coalesce(ws.metadata_json->'indicators', '[]'::jsonb)
                                  ? :indicator
                            order by ws.sort_order asc, ws.symbol asc
                            """
                        ),
                        {
                            "customer_slug": self._customer_slug,
                            "watchlist_code": self._watchlist_code,
                            "indicator": indicator,
                        },
                    )
                ).all()
        except SQLAlchemyError as error:
            raise PostgresUniverseError(
                f"Local PostgreSQL {indicator} portfolio query failed"
            ) from error

        if not rows:
            raise PostgresUniverseError(
                f"Local PostgreSQL has no active {indicator} watchlist symbols"
            )
        try:
            return tuple(
                PortfolioAllocation(
                    symbol=str(row.symbol).strip().upper(),
                    weight_percent=Decimal(str(row.target_weight_percent)),
                )
                for row in rows
            )
        except (InvalidOperation, ValueError, TypeError) as error:
            raise PostgresUniverseError(
                f"Local PostgreSQL contains an invalid {indicator} target weight"
            ) from error

    async def get_holding_quantity(self, symbol: str) -> Decimal:
        """Return the current authoritative holding quantity for one symbol."""

        try:
            async with self._engine.connect() as connection:
                value = await connection.scalar(
                    text(
                        """
                        select coalesce(h.quantity, 0)
                        from stock.customer c
                        left join stock.customer_holding h
                          on h.customer_id = c.id
                         and h.symbol = :symbol
                         and h.status = 'active'
                        where c.slug = :customer_slug
                          and c.status = 'active'
                        """
                    ),
                    {
                        "customer_slug": self._customer_slug,
                        "symbol": symbol.strip().upper(),
                    },
                )
        except SQLAlchemyError as error:
            raise PostgresUniverseError(
                f"Local PostgreSQL holding query failed for {symbol}"
            ) from error
        return Decimal(str(value or 0))


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
