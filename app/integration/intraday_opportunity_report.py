"""Weekly effectiveness report for operational intraday paper opportunities."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.common.clock import SystemClock
from app.common.settings import AppSettings, Environment
from app.contracts import IntradayOpportunity, IntradayOpportunityStatus
from app.persistence import create_database_engine, create_session_factory

from .intraday_opportunity_store import PostgresIntradayOpportunityStore

_NEW_YORK = ZoneInfo("America/New_York")
_FOUR_PLACES = Decimal("0.0001")


async def load_intraday_opportunity_report(*, days: int = 7) -> dict[str, Any]:
    """Load the most recent calendar window from local PostgreSQL."""

    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    settings = AppSettings()
    database = create_database_engine(
        settings.database_url.get_secret_value(),
        require_ssl=settings.environment is Environment.PRODUCTION,
    )
    store = PostgresIntradayOpportunityStore(create_session_factory(database))
    try:
        if not await store.is_ready():
            raise RuntimeError("intraday opportunity migration is not applied")
        end_date = SystemClock().now().astimezone(_NEW_YORK).date()
        start_date = end_date - timedelta(days=days - 1)
        opportunities: list[IntradayOpportunity] = []
        current = start_date
        while current <= end_date:
            opportunities.extend(await store.list_session(current))
            current += timedelta(days=1)
        return summarize_intraday_opportunities(
            opportunities,
            start_date=start_date,
            end_date=end_date,
        )
    finally:
        await database.dispose()


def summarize_intraday_opportunities(
    opportunities: Iterable[IntradayOpportunity],
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Calculate closed-trade effectiveness without treating open marks as outcomes."""

    items = tuple(
        sorted(
            (
                item
                for item in opportunities
                if start_date <= item.session_date <= end_date
            ),
            key=lambda item: (item.opened_at, item.symbol, str(item.opportunity_id)),
        )
    )
    closed = tuple(
        item for item in items if item.status is IntradayOpportunityStatus.CLOSED
    )
    open_items = tuple(
        item for item in items if item.status is IntradayOpportunityStatus.OPEN
    )
    wins = tuple(item for item in closed if item.net_pnl > 0)
    losses = tuple(item for item in closed if item.net_pnl < 0)
    breakeven = tuple(item for item in closed if item.net_pnl == 0)
    gross_profit = sum((item.net_pnl for item in wins), Decimal("0"))
    gross_loss = abs(sum((item.net_pnl for item in losses), Decimal("0")))
    total_net = sum((item.net_pnl for item in items), Decimal("0"))
    total_gross = sum((item.gross_pnl for item in items), Decimal("0"))
    closed_net = sum((item.net_pnl for item in closed), Decimal("0"))
    open_mark_net = sum((item.net_pnl for item in open_items), Decimal("0"))

    return {
        "period": {"from": start_date.isoformat(), "to": end_date.isoformat()},
        "mode": "PAPER",
        "total_opportunities": len(items),
        "closed": len(closed),
        "open": len(open_items),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "effectiveness_rate_percent": _average_percent(
            Decimal(len(wins)), Decimal(len(closed))
        ),
        "expectancy_net_percent": _average(item.net_pnl_percent for item in closed),
        "profit_factor": (
            _decimal_text(gross_profit / gross_loss) if gross_loss > 0 else None
        ),
        "total_gross_pnl": _decimal_text(total_gross),
        "total_net_pnl": _decimal_text(total_net),
        "closed_realized_net_pnl": _decimal_text(closed_net),
        "open_mark_net_pnl": _decimal_text(open_mark_net),
        "average_mfe_percent": _average(item.mfe_percent for item in closed),
        "average_mae_percent": _average(item.mae_percent for item in closed),
        "operations": tuple(_operation(item) for item in items),
    }


def _operation(item: IntradayOpportunity) -> dict[str, Any]:
    return {
        "opportunity_id": str(item.opportunity_id),
        "session_date": item.session_date.isoformat(),
        "symbol": item.symbol,
        "strategy_id": item.strategy_id,
        "side": item.side.value,
        "status": item.status.value,
        "opened_at": item.opened_at.isoformat(),
        "closed_at": item.closed_at.isoformat() if item.closed_at is not None else None,
        "close_reason": item.close_reason.value if item.close_reason is not None else None,
        "entry_price": _decimal_text(item.entry_price),
        "current_price": _decimal_text(item.current_price),
        "exit_price": _decimal_text(item.exit_price) if item.exit_price is not None else None,
        "net_pnl": _decimal_text(item.net_pnl),
        "net_pnl_percent": _decimal_text(item.net_pnl_percent),
        "mfe_percent": _decimal_text(item.mfe_percent),
        "mae_percent": _decimal_text(item.mae_percent),
    }


def _average(values: Iterable[Decimal]) -> str | None:
    items = tuple(values)
    if not items:
        return None
    return _decimal_text(sum(items, Decimal("0")) / Decimal(len(items)))


def _average_percent(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator == 0:
        return None
    return _decimal_text(numerator / denominator * Decimal("100"))


def _decimal_text(value: Decimal) -> str:
    return str(value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP))
