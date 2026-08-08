from __future__ import annotations

import asyncio
import json
import selectors
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.alert_engine.confirmed import buy_maturity
from app.common.settings import AppSettings
from app.contracts import AlertKind, AnalysisHorizon, LocalAlert, PatternDirection
from app.persistence import create_database_engine

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent / "data.json"
ALERT_LEDGER = Path(
    r"\\wsl.localhost\Ubuntu\home\lgonz\Projects\market-bot\.runtime\alerts\marketbot-alerts-2026-08-07.ndjson"
)
BUENOS_AIRES = ZoneInfo("America/Argentina/Buenos_Aires")
FRIDAY_BAR_AT = datetime(2026, 8, 7, 4, tzinfo=UTC)


def metric_map(value: object) -> dict[str, object]:
    metrics = getattr(value, "metrics", ())
    return {item.name: item.value for item in metrics}


def decimal_value(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int, float)):
        try:
            return Decimal(str(value))
        except Exception:
            return None
    return None


def alert_price(alert: LocalAlert) -> tuple[Decimal | None, str | None]:
    direct_metrics = metric_map(alert)
    for name in ("current_price", "reference_price"):
        price = decimal_value(direct_metrics.get(name))
        if price is not None:
            return price, f"alert.{name}"
    by_horizon = {analysis.horizon: analysis for analysis in alert.component_analyses}
    for horizon in (
        AnalysisHorizon.INTRADAY,
        AnalysisHorizon.SWING,
        AnalysisHorizon.LONG_TERM,
    ):
        analysis = by_horizon.get(horizon)
        if analysis is None:
            continue
        price = decimal_value(metric_map(analysis).get("reference_price"))
        if price is not None:
            return price, f"{horizon.value.lower()}.reference_price"
    return None, None


def alert_direction(alert: LocalAlert) -> str:
    if buy_maturity(alert) is not None or alert.kind in {
        AlertKind.LONG_BUY_ZONE,
        AlertKind.ENTRY_WATCH,
        AlertKind.ENTRY_OPPORTUNITY_PROGRESS,
    }:
        return "LONG"
    if alert.kind is AlertKind.BEARISH_CONSENSUS:
        return "SHORT"
    if alert.kind is AlertKind.ENTRY_OPPORTUNITY_CLOSED:
        return "NEUTRAL"
    by_horizon = {analysis.horizon: analysis for analysis in alert.component_analyses}
    preferred = (
        (AnalysisHorizon.SWING,)
        if alert.kind is AlertKind.SWING_SETUP
        else (AnalysisHorizon.INTRADAY, AnalysisHorizon.SWING, AnalysisHorizon.LONG_TERM)
    )
    for horizon in preferred:
        analysis = by_horizon.get(horizon)
        if analysis is None:
            continue
        if analysis.direction is PatternDirection.BULLISH:
            return "LONG"
        if analysis.direction is PatternDirection.BEARISH:
            return "SHORT"
    return "NEUTRAL"


def alert_row(alert: LocalAlert) -> dict[str, object]:
    price, source = alert_price(alert)
    maturity = buy_maturity(alert)
    return {
        "alert_id": str(alert.alert_id),
        "symbol": alert.symbol,
        "created_at_ba": alert.created_at.astimezone(BUENOS_AIRES).isoformat(),
        "kind": alert.kind.value,
        "title": alert.title,
        "severity": alert.severity.value,
        "maturity": maturity.value if maturity is not None else None,
        "direction": alert_direction(alert),
        "price": float(price) if price is not None else None,
        "price_source": source,
    }


async def load_friday_closes(symbols: list[str]) -> dict[str, dict[str, object]]:
    settings = AppSettings()
    engine = create_database_engine(settings.database_url.get_secret_value(), require_ssl=False)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        select symbol, timestamp, close, source, feed, is_final
                        from market_bot.market_bars
                        where symbol = any(:symbols)
                          and timeframe = '1Day'
                          and timestamp = :friday_bar_at
                        """
                    ),
                    {"symbols": symbols, "friday_bar_at": FRIDAY_BAR_AT},
                )
            ).mappings().all()
        return {
            str(row["symbol"]): {
                "close": float(row["close"]),
                "timestamp": row["timestamp"].isoformat(),
                "source": str(row["source"]),
                "feed": str(row["feed"]),
                "is_final": bool(row["is_final"]),
            }
            for row in rows
        }
    finally:
        await engine.dispose()


def load_alerts() -> list[LocalAlert]:
    return [
        LocalAlert.model_validate_json(line)
        for line in ALERT_LEDGER.read_bytes().splitlines()
        if line.strip()
    ]


async def main() -> None:
    alerts = load_alerts()
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for alert in alerts:
        grouped[alert.symbol].append(alert_row(alert))
    for rows in grouped.values():
        rows.sort(key=lambda item: str(item["created_at_ba"]))

    closes = await load_friday_closes(sorted(grouped))
    details: list[dict[str, object]] = []
    for symbol in sorted(grouped):
        rows = grouped[symbol]
        priced = [row for row in rows if row["price"] is not None]
        buys = [row for row in priced if row["maturity"] is not None]
        first_alert = priced[0] if priced else rows[0]
        first_buy = buys[0] if buys else None
        selected = first_buy or first_alert
        close = closes.get(symbol)
        details.append(
            {
                "symbol": symbol,
                "alert_count": len(rows),
                "first_alert_at_ba": rows[0]["created_at_ba"],
                "first_alert_kind": rows[0]["kind"],
                "first_alert_price": first_alert["price"],
                "first_alert_price_source": first_alert["price_source"],
                "first_buy_at_ba": first_buy["created_at_ba"] if first_buy else None,
                "first_buy_kind": first_buy["kind"] if first_buy else None,
                "first_buy_maturity": first_buy["maturity"] if first_buy else None,
                "first_buy_price": first_buy["price"] if first_buy else None,
                "selected_basis": "FIRST_BUY" if first_buy else "FIRST_ALERT",
                "selected_at_ba": selected["created_at_ba"],
                "selected_kind": selected["kind"],
                "selected_direction": selected["direction"],
                "selected_price": selected["price"],
                "selected_price_source": selected["price_source"],
                "friday_close": close["close"] if close else None,
                "close_timestamp": close["timestamp"] if close else None,
                "close_source": (
                    f"{close['source']}/{close['feed']} final={close['is_final']}"
                    if close
                    else None
                ),
                "last_alert_at_ba": rows[-1]["created_at_ba"],
                "last_alert_kind": rows[-1]["kind"],
                "alert_kinds": ", ".join(
                    f"{kind}:{count}"
                    for kind, count in sorted(
                        Counter(row["kind"] for row in rows).items()
                    )
                ),
            }
        )

    payload = {
        "metadata": {
            "report_date": "2026-08-07",
            "timezone": "America/Argentina/Buenos_Aires",
            "ledger_path": str(ALERT_LEDGER),
            "ledger_alert_count": len(alerts),
            "unique_ticker_count": len(details),
            "close_coverage_count": len(closes),
            "selection_rule": (
                "Primera alerta L1-L4 con precio; si no existe, primera alerta con precio."
            ),
            "close_rule": "Barra final 1Day persistida en PostgreSQL a 2026-08-07T04:00:00Z.",
        },
        "details": details,
        "alerts": sorted(
            (row for rows in grouped.values() for row in rows),
            key=lambda item: str(item["created_at_ba"]),
        ),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload["metadata"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    with asyncio.Runner(
        loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
    ) as runner:
        runner.run(main())
