"""Read-only terminal P/L board for filled, still-open LONG opportunity legs.

Run with python -m app.integration.open_buy_pl_monitor. The process only reads
local PostgreSQL; it never modifies a thesis, opens orders or creates consumers.
"""

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
class BuyRow:
    symbol: str
    horizon: str
    entered_at: datetime
    entry: Decimal
    current: Decimal
    price_at: datetime | None

    @property
    def pnl_percent(self) -> Decimal:
        return self.pnl_per_share / self.entry * 100

    @property
    def pnl_per_share(self) -> Decimal:
        return self.current - self.entry


def _date(value: object) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def buy_rows(payloads: Iterable[dict[str, Any]]) -> list[BuyRow]:
    """Use filled legs, never ARMED/IN_ZONE checkpoints or a parent's mark."""
    rows: list[BuyRow] = []
    for item in payloads:
        if item.get("status") == "CLOSED" or item.get("trade_side", "LONG") != "LONG":
            continue
        for leg in item.get("legs", []):
            if (
                leg.get("status") != "OPEN"
                or leg.get("closed_at") is not None
                or leg.get("trade_side", "LONG") != "LONG"
                or leg.get("signal_family") == "CORE_SHORT"
            ):
                continue
            entered_at = _date(leg.get("opened_at"))
            if entered_at is None:
                continue
            confirmations = [
                cp
                for cp in item.get("checkpoints", [])
                if cp.get("status") == "OPEN"
                and _date(cp.get("reached_at")) == entered_at
                and cp.get("trade_side", "LONG") == "LONG"
                and (
                    cp.get("level") in {"L1", "L2", "L3", "L4"}
                    or cp.get("swing_trade_maturity") in {"ST3", "ST4"}
                    or cp.get("countertrend_maturity") in {"CT2", "CT3", "CT4"}
                )
            ]
            if not confirmations:
                continue
            try:
                entry = Decimal(str(leg.get("entry_price")))
                current = Decimal(str(leg.get("current_price")))
            except InvalidOperation:
                continue
            if not (entry.is_finite() and current.is_finite() and entry > 0 and current > 0):
                continue
            rows.append(
                BuyRow(
                    symbol=str(item["symbol"]),
                    horizon={
                        "VOLUME_STRUCTURE": "OBV",
                        "LONG_TERM": "LONG",
                        "INTRADAY": "INTRA",
                    }.get(str(leg.get("horizon")), str(leg.get("horizon", "-"))),
                    entered_at=entered_at,
                    entry=entry,
                    current=current,
                    price_at=_date(item.get("last_market_bar_at")),
                )
            )
    grouped: dict[tuple[str, datetime, Decimal, Decimal], BuyRow] = {}
    for row in rows:
        key = (row.symbol, row.entered_at, row.entry, row.current)
        previous = grouped.get(key)
        if previous is not None:
            row = BuyRow(
                row.symbol,
                "+".join(sorted(set([*previous.horizon.split("+"), row.horizon]))),
                row.entered_at,
                row.entry,
                row.current,
                row.price_at,
            )
        grouped[key] = row
    return sorted(grouped.values(), key=lambda row: (row.entered_at, row.symbol), reverse=True)


def render_panel(
    rows: list[BuyRow], *, now: datetime, page: int = 0, page_size: int = 30, color: bool = False
) -> str:
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page %= pages
    shown = rows[page * page_size : (page + 1) * page_size]
    lines = [
        "P/L | COMPRAS CONFIRMADAS ABIERTAS | SIMULADAS",
        f"{now.astimezone(_LOCAL):%d/%m/%Y %H:%M:%S} ART | "
        f"{len(rows)} entradas | pagina {page + 1}/{pages} | refresco 5 s",
        "Fecha de entrada y ultimo precio: Argentina. P/L bruto; USD por accion.",
        "",
        f"{'TICKER':<8} {'HORIZONTE':<10} {'ENTRADA ART':<15} {'PRECIO ENT.':>12} "
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
            f"{row.symbol:<8} {row.horizon:<10} {row.entered_at.astimezone(_LOCAL):%d/%m/%y %H:%M} "
            f"{row.entry:>12.4f} {row.current:>12.4f} {pnl} "
            f"{row.pnl_per_share:>+11.4f} {price_at:>16}"
        )
    if not rows:
        lines.append("Sin compras confirmadas abiertas.")
    lines.extend(["", "Se retiran al cerrar. Horizontes con la misma entrada se agrupan."])
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
                rows = buy_rows(payloads)
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
