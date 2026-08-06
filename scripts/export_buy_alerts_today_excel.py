#!/usr/bin/env python

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import nats
from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from openpyxl import Workbook

from app.contracts import EventEnvelope, LocalAlert, NamedValue

ALPACA_KEYS = {
    "MARKETBOT_ALPACA_API_KEY_ID": "MARKETBOT_ALPACA_API_SECRET_KEY",
    "APCA_API_KEY_ID": "APCA_API_SECRET_KEY",
}
ALPACA_FEED_ENV = ("MARKETBOT_ALPACA_DATA_FEED", "ALPACA_DATA_FEED")
DEFAULT_FEED = "sip"
BUY_KINDS = {
    "LONG_BUY_ZONE",
    "SWING_SETUP",
    "ENTRY_CONFIRMED",
    "HIGH_CONVICTION_BUY",
    "LONG_PORTFOLIO_BUY",
    "PATREON_CAPS_BUY",
    "ENTRY_WATCH",
}
BUY_KIND_PREFIXES = ("SWING_SETUP", "ENTRY_CONFIRMED", "BUY", "BUY_ZONE", "PORTFOLIO_BUY")
ENTRY_PRICE_KEYS = (
    "current_price",
    "entry_price",
    "reference_price",
)
ZONE_KEYS = ("buy_zone_low", "buy_zone_high", "entry_zone_low", "entry_zone_high")
INVALIDATION_KEYS = ("invalidation", "invalidation_level", "invalidacion")
OBJECTIVE_KEYS = ("objective", "target_2r", "objective_level")


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"")
    return values


def load_env() -> dict[str, str]:
    merged: dict[str, str] = {}
    paths = [
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parents[1] / ".env.local",
        Path(r"C:\Users\lgonz\Projects\stock-analyzer\apps\account-desktop\.env.local"),
    ]
    for path in paths:
        merged.update(load_env_file(path))
    merged.update({k: v for k, v in os.environ.items() if v})
    return merged


def find_alpaca_credentials(env: dict[str, str]) -> tuple[str, str] | None:
    for key, secret_key in ALPACA_KEYS.items():
        secret = env.get(secret_key)
        key_value = env.get(key)
        if key_value and secret:
            return key_value.strip(), secret.strip()
    return None


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except (TypeError, InvalidOperation):
            return None
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (TypeError, InvalidOperation):
            return None
    return None


def as_float(value: object) -> float | None:
    decimal = to_decimal(value)
    if decimal is None:
        return None
    try:
        return float(decimal)
    except (TypeError, ValueError):
        return None


def metrics_from(values: Iterable[NamedValue]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in values:
        result[item.name] = item.value
    return result


def get_first_metric(
    metrics_maps: list[dict[str, Any]], keys: Iterable[str]
) -> tuple[Decimal | None, str | None]:
    for metrics in metrics_maps:
        for key in keys:
            if key in metrics:
                value = to_decimal(metrics[key])
                if value is not None:
                    return value, key
    return None, None


def derive_entry_price(
    alert: LocalAlert, component_metrics: list[dict[str, Any]]
) -> tuple[Decimal | None, str]:
    all_metrics = [metrics_from(alert.metrics), *component_metrics]
    price, source = get_first_metric(all_metrics, ENTRY_PRICE_KEYS)
    if price is not None:
        return price, source
    for metrics in all_metrics:
        low = to_decimal(metrics.get("buy_zone_low"))
        high = to_decimal(metrics.get("buy_zone_high"))
        if low is None or high is None:
            continue
        return (low + high) / Decimal("2"), "zone_midpoint"
    return None, ""


def reason_set(values: Iterable[str] | None) -> str:
    if not values:
        return ""
    return "; ".join(values)


def is_buy_alert(alert: LocalAlert) -> bool:
    kind = str(alert.kind)
    if kind in BUY_KINDS:
        return True
    if any(kind.startswith(prefix) for prefix in BUY_KIND_PREFIXES):
        return True
    if "buy" in str(alert.title).lower() or "buy" in str(alert.message).lower():
        return True
    return any("BUY" in item.upper() for item in alert.reasons)


def is_protect(alert: LocalAlert) -> bool:
    return str(alert.kind) == "PORTFOLIO_PROTECT"


def extract_horizon_data(alert: LocalAlert) -> dict[str, dict[str, Any]]:
    by_horizon: dict[str, dict[str, Any]] = {}
    for analysis in alert.component_analyses:
        by_horizon[str(analysis.horizon)] = {
            "metrics": metrics_from(analysis.metrics),
            "reasons": list(analysis.reasons),
            "score": str(analysis.score),
            "verdict": str(analysis.verdict),
        }
    return by_horizon


async def fetch_day_close_prices(
    *,
    symbols_by_day: dict[str, set[date]],
    headers: dict[str, str],
    feed: str,
    max_concurrency: int = 6,
) -> dict[tuple[str, date], float | None]:
    results: dict[tuple[str, date], float | None] = {}
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_one(symbol: str, day: date) -> tuple[str, date, float | None]:
        async with semaphore:
            start = day.isoformat()
            end = (day + timedelta(days=1)).isoformat()
            url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
            params = {
                "timeframe": "1Day",
                "start": start,
                "end": end,
                "adjustment": "raw",
                "feed": feed,
                "sort": "asc",
                "limit": 10,
            }
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
            bars = data.get("bars", [])
            if not bars:
                return symbol, day, None
            close_value = to_decimal(bars[0].get("c"))
            return symbol, day, as_float(close_value) if close_value is not None else None

    tasks = []
    for symbol, days in symbols_by_day.items():
        for day in sorted(days):
            tasks.append(fetch_one(symbol, day))
    fetched = await asyncio.gather(*tasks)
    for symbol, day, close in fetched:
        results[(symbol, day)] = close
    return results


async def load_alerts(
    nats_url: str,
    target_date_utc: date,
    *,
    include_after: timedelta = timedelta(days=1),
) -> list[tuple[LocalAlert, datetime]]:
    start = datetime(target_date_utc.year, target_date_utc.month, target_date_utc.day, tzinfo=UTC)
    end = start + include_after
    nc = await nats.connect(servers=[nats_url], connect_timeout=2)
    js = nc.jetstream()
    consumer_name = f"marketbot-buy-export-{int(datetime.now(tz=UTC).timestamp())}"
    config = ConsumerConfig(
        durable_name=consumer_name,
        deliver_policy=DeliverPolicy.BY_START_TIME,
        opt_start_time=start,
        ack_policy=AckPolicy.EXPLICIT,
    )
    subscription = await js.pull_subscribe(
        "marketbot.v1.alert.local.>",
        durable=consumer_name,
        config=config,
    )
    alerts: list[tuple[LocalAlert, datetime]] = []
    seen: set[str] = set()
    try:
        while True:
            try:
                messages = await subscription.fetch(batch=300, timeout=3)
            except NatsTimeoutError:
                break
            if not messages:
                break
            for message in messages:
                try:
                    envelope = EventEnvelope.model_validate_json(message.data)
                    payload = envelope.payload
                    if isinstance(payload, LocalAlert):
                        alert = payload
                    elif isinstance(payload, dict):
                        alert = LocalAlert.model_validate(payload, strict=False)
                    else:
                        continue
                except Exception:
                    await message.ack()
                    continue

                created_at = parse_datetime(str(alert.created_at))
                if created_at < start or created_at >= end:
                    await message.ack()
                    if created_at >= end:
                        await nc.close()
                        return alerts
                    continue

                if alert.alert_id in seen:
                    await message.ack()
                    continue
                seen.add(alert.alert_id)
                alerts.append((alert, created_at))
                await message.ack()
    finally:
        await nc.close()
    return alerts


def load_alerts_from_ndjson(target_date: date) -> list[tuple[LocalAlert, datetime]]:
    runtime_path = (
        Path(__file__).resolve().parents[1]
        / ".runtime"
        / "alerts"
        / f"marketbot-alerts-{target_date.isoformat()}.ndjson"
    )
    if not runtime_path.exists():
        return []
    alerts: list[tuple[LocalAlert, datetime]] = []
    with runtime_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            if not line.strip():
                continue
            data = json.loads(line)
            try:
                alert = LocalAlert.model_validate(data, strict=False)
            except Exception as exc:
                print(f"WARN: alerta inválida en backup NDJSON: {exc}")
                continue
            created_at = parse_datetime(str(alert.created_at))
            if created_at.date() != target_date:
                continue
            alerts.append((alert, created_at))
    return alerts


def build_output_rows(
    alerts: list[tuple[LocalAlert, datetime]],
    closes: dict[tuple[str, date], float | None],
    local_tz: ZoneInfo,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for alert, created_at in alerts:
        if is_protect(alert):
            continue
        component_by_horizon = extract_horizon_data(alert)
        component_metrics = [item["metrics"] for item in component_by_horizon.values()]
        entry_price, entry_source = derive_entry_price(alert, component_metrics)
        entry_price_f = as_float(entry_price)
        metric_sources = [metrics_from(alert.metrics), *component_metrics]
        invalidation, invalidation_source = get_first_metric(
            metric_sources, INVALIDATION_KEYS
        )
        objective, objective_source = get_first_metric(metric_sources, OBJECTIVE_KEYS)
        alert_day = created_at.date()
        close = closes.get((alert.symbol, alert_day))
        pn_l: float | None = None
        if close is not None and entry_price is not None and entry_price != 0:
            pn_l = (Decimal(str(close)) - entry_price) / entry_price * Decimal("100")

        long_reasons = reason_set(component_by_horizon.get("LONG_TERM", {}).get("reasons"))
        swing_reasons = reason_set(component_by_horizon.get("SWING", {}).get("reasons"))
        intraday_reasons = reason_set(component_by_horizon.get("INTRADAY", {}).get("reasons"))
        dilution_reasons = reason_set(component_by_horizon.get("DILUTION", {}).get("reasons"))

        row: dict[str, Any] = {
            "alert_datetime_utc": created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "alert_datetime_local": created_at.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S"),
            "alert_date": alert_day.isoformat(),
            "symbol": alert.symbol,
            "kind": str(alert.kind),
            "severity": str(alert.severity),
            "score": str(alert.score),
            "title": alert.title,
            "message": alert.message,
            "horizons": ",".join(map(str, alert.horizons)),
            "entry_price": entry_price_f,
            "entry_price_source": entry_source,
            "invalidation": as_float(invalidation),
            "invalidation_source": invalidation_source,
            "objective": as_float(objective),
            "objective_source": objective_source,
            "close_session_date": alert_day.isoformat(),
            "close_price": close,
            "gain_loss_pct": float(pn_l) if pn_l is not None else None,
            "gain_loss_abs": (
                close - entry_price_f
                if close is not None and entry_price_f is not None
                else None
            ),
            "alert_reasons": reason_set(alert.reasons),
            "long_term_reasons": long_reasons,
            "swing_reasons": swing_reasons,
            "intraday_reasons": intraday_reasons,
            "dilution_reasons": dilution_reasons,
            "long_term_metrics": json.dumps(
                component_by_horizon.get("LONG_TERM", {}).get("metrics", {}),
                ensure_ascii=False,
            ),
            "swing_metrics": json.dumps(
                component_by_horizon.get("SWING", {}).get("metrics", {}),
                ensure_ascii=False,
            ),
            "intraday_metrics": json.dumps(
                component_by_horizon.get("INTRADAY", {}).get("metrics", {}),
                ensure_ascii=False,
            ),
            "alert_id": str(alert.alert_id),
            "deduplication_key": alert.deduplication_key,
        }
        rows.append(row)
    return rows


def write_excel(rows: list[dict[str, Any]], output: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "buy_alerts"
    headers = [
        "alert_datetime_utc",
        "alert_datetime_local",
        "alert_date",
        "symbol",
        "kind",
        "severity",
        "score",
        "title",
        "message",
        "horizons",
        "entry_price",
        "entry_price_source",
        "invalidation",
        "invalidation_source",
        "objective",
        "objective_source",
        "close_session_date",
        "close_price",
        "gain_loss_pct",
        "gain_loss_abs",
        "alert_reasons",
        "long_term_reasons",
        "swing_reasons",
        "intraday_reasons",
        "dilution_reasons",
        "long_term_metrics",
        "swing_metrics",
        "intraday_metrics",
        "alert_id",
        "deduplication_key",
    ]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(column) for column in headers])
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export buy alerts to Excel")
    parser.add_argument("--date", help="Date of alerts in YYYY-MM-DD (default: local today)")
    parser.add_argument("--timezone", default="America/Buenos_Aires")
    parser.add_argument("--output", help="Output XLSX path")
    return parser.parse_args()


def default_output_path(target: date) -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / f"marketbot-buy-alerts-{target.isoformat()}.xlsx"
    )


async def main_async() -> int:
    args = parse_args()
    env = load_env()
    tz = ZoneInfo(args.timezone)
    local_today = datetime.now(tz=tz).date()
    target = datetime.fromisoformat(args.date).date() if args.date else local_today

    nats_url = (
        env.get("MARKETBOT_NATS_URL")
        or env.get("NATS_URL")
        or "nats://127.0.0.1:4222"
    )
    credentials = find_alpaca_credentials(env)
    if credentials is None:
        raise SystemExit(
            "No se encontraron credenciales de Alpaca "
            "(APCA_* o MARKETBOT_ALPACA_*)."
        )
    feed = next(
        (env.get(item, "").strip() for item in ALPACA_FEED_ENV if env.get(item)),
        DEFAULT_FEED,
    )
    headers = {
        "APCA-API-KEY-ID": credentials[0],
        "APCA-API-SECRET-KEY": credentials[1],
    }

    alerts: list[tuple[LocalAlert, datetime]]
    try:
        alerts = await load_alerts(nats_url=nats_url, target_date_utc=target)
    except Exception as exc:
        print(f"WARN: no se pudo leer NATS ({exc}). Cargando desde backup .runtime si existe.")
        alerts = load_alerts_from_ndjson(target)

    selected = [
        (alert, created_at)
        for alert, created_at in alerts
        if is_buy_alert(alert) and not is_protect(alert)
    ]
    selected.sort(key=lambda item: item[1])
    if not selected:
        print(f"No se encontraron alertas de BUY para {target}")
    symbols_by_day: dict[str, set[date]] = {}
    for alert, created_at in selected:
        symbols_by_day.setdefault(alert.symbol, set()).add(created_at.date())
    closes = (
        await fetch_day_close_prices(
            symbols_by_day=symbols_by_day,
            headers=headers,
            feed=feed,
            max_concurrency=8,
        )
        if symbols_by_day
        else {}
    )
    rows = build_output_rows(selected, closes, tz)
    output = default_output_path(target) if not args.output else Path(args.output)
    write_excel(rows, output)
    print(f"Se exportó {len(rows)} alertas a {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
