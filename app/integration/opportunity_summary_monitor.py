"""Read-only summary of all active opportunities, including unconfirmed tracking."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.common.settings import AppSettings
from app.persistence import create_database_engine

_LOCAL = ZoneInfo("America/Argentina/Buenos_Aires")


@dataclass(frozen=True)
class OpportunityRow:
    symbol: str
    horizon: str
    side: str
    entered_at: datetime
    entry: Decimal
    current: Decimal
    price_at: datetime | None

    @property
    def pnl_percent(self) -> Decimal:
        return self.pnl_per_share / self.entry * 100

    @property
    def pnl_per_share(self) -> Decimal:
        return (self.entry - self.current) if self.side == "SHORT" else (self.current - self.entry)


def _date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def opportunity_rows(payloads: Iterable[dict[str, Any]]) -> list[OpportunityRow]:
    """One row per active thesis; entry means admission to opportunity tracking."""
    rows: list[OpportunityRow] = []
    for item in payloads:
        if item.get("status") == "CLOSED":
            continue
        entered_at = _date(item.get("armed_at"))
        if entered_at is None:
            continue
        try:
            entry = Decimal(str(item.get("original_price")))
            current = Decimal(str(item.get("current_price")))
        except InvalidOperation:
            continue
        if not (entry.is_finite() and current.is_finite() and entry > 0 and current > 0):
            continue
        rows.append(
            OpportunityRow(
                symbol=str(item["symbol"]),
                horizon=str(item.get("status", "-")),
                side=str(item.get("trade_side", "LONG")),
                entered_at=entered_at,
                entry=entry,
                current=current,
                price_at=_date(item.get("last_market_bar_at")),
            )
        )
    return sorted(rows, key=lambda row: (row.entered_at, row.symbol, row.side), reverse=True)


def render_panel(
    rows: list[OpportunityRow],
    *,
    now: datetime,
    page: int = 0,
    page_size: int = 30,
    color: bool = False,
) -> str:
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page %= pages
    shown = rows[page * page_size : (page + 1) * page_size]
    lines = [
        "P/L | RESUMEN OPPORTUNITIES | TODAS LAS ACTIVAS",
        f"{now.astimezone(_LOCAL):%d/%m/%Y %H:%M:%S} ART | "
        f"{len(rows)} oportunidades | pagina {page + 1}/{pages} | refresco 5 s",
        "Ingreso al seguimiento (no compra). P/L desde precio inicial; USD por accion. Horas ART.",
        "",
        f"{'TICKER':<8} {'LADO':<5} {'ESTADO':<10} {'INGRESO ART':<15} {'PRECIO ENT.':>12} "
        f"{'PRECIO ACT.':>12} {'P/L %':>10} {'USD/ACC.':>11} {'ULTIMO DATO ART':>16}",
        "-" * 104,
    ]
    for row in shown:
        pnl = f"{row.pnl_percent:+.2f}%"
        pnl = f"{pnl:>10}"
        if color:
            style = "32" if row.pnl_percent >= 0 else "31"
            pnl = f"\033[{style}m{pnl}\033[0m"
        price_at = (
            row.price_at.astimezone(_LOCAL).strftime("%d/%m %H:%M") if row.price_at else "SIN HORA"
        )
        lines.append(
            f"{row.symbol:<8} {row.side:<5} {row.horizon:<10} "
            f"{row.entered_at.astimezone(_LOCAL):%d/%m/%y %H:%M} "
            f"{row.entry:>12.4f} {row.current:>12.4f} {pnl} "
            f"{row.pnl_per_share:>+11.4f} {price_at:>16}"
        )
    if not rows:
        lines.append("Sin oportunidades activas.")
    lines.extend(
        ["", "Se retiran al cerrar. Todas las oportunidades activas, con paginacion automatica."]
    )
    return "\n".join(lines) + "\n"


async def run(*, once: bool = False) -> None:
    settings = AppSettings()
    database = create_database_engine(settings.database_url.get_secret_value(), require_ssl=False)
    page = 0
    try:
        while True:
            try:
                async with database.connect() as connection:
                    payloads = (
                        (
                            await connection.execute(
                                text(
                                    "SELECT payload FROM market_bot.entry_opportunities "
                                    "WHERE status <> 'CLOSED'"
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                rows = opportunity_rows(payloads)
                size = max(1, shutil.get_terminal_size((120, 40)).lines - 10)
                output = render_panel(
                    rows,
                    now=datetime.now(UTC),
                    page=page,
                    page_size=size,
                    color=sys.stdout.isatty(),
                )
                print(("" if once else "\033[2J\033[H") + output, end="", flush=True)
                page += 1
            except Exception as error:
                if once:
                    raise
                print(
                    f"\nP/L SIN ACTUALIZAR: {type(error).__name__}. Reintentando en 5 s.",
                    flush=True,
                )
            if once:
                return
            await asyncio.sleep(5)
    finally:
        await database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    with suppress(KeyboardInterrupt):
        asyncio.run(run(once=parser.parse_args().once))
