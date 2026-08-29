"""Standalone Alpaca-to-TradingView geometry projection for MarketBot."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast
from zoneinfo import ZoneInfo

from app.alpaca_market_data.normalizer import AlpacaEventNormalizer
from app.alpaca_market_data.rest import AlpacaRestClient
from app.alpaca_market_data.transports import HttpxTransport
from app.common.market_session import is_regular_session
from app.common.settings import AppSettings
from app.contracts import (
    AnalysisResult,
    GeriAssessment,
    MarketBar,
    SwingChannelAssessment,
    SwingTradeAssessment,
)
from app.integration.bar_aggregator import RegularSessionFourHourAggregator
from app.integration.engine_assembly import MarketBotAssembly
from app.swing_4h_geri_engine import Swing4HGeriContext
from app.swing_channel_4h_engine import SwingChannel4HContext
from app.swing_engine import SwingContext
from app.swing_trade_engine import SwingTradeContext

_NEW_YORK = ZoneInfo("America/New_York")

TRADINGVIEW_COLUMNS = (
    "ticker",
    "modoNiveles",
    "marcoCalculo",
    "zonaLowManual",
    "zonaHighManual",
    "soporteManual",
    "invalidationManual",
    "resistenciaManual",
    "liquidezManual",
    "avwapPivotManual",
    "avwapBreakoutManual",
    "extendedTarget",
    "asOf",
    "fuenteDatos",
    "swingSma20",
    "swingSma50",
    "swingTarget",
    "pivotLowAnchorAt",
    "breakoutAnchorAt",
    "stImpulseLow",
    "stImpulseHigh",
    "stImpulseLowAt",
    "stImpulseHighAt",
    "stFib618",
    "stFib50",
    "stFib1618",
    "stZoneLow",
    "stZoneHigh",
    "stSupport20",
    "stResistance20",
    "stInvalidation",
    "stPrimaryTarget",
    "stExtendedTarget",
    "channelZoneLow",
    "channelZoneHigh",
    "channelSupport",
    "channelMiddle",
    "channelResistance",
    "channelInvalidation",
    "geriZoneLow",
    "geriZoneHigh",
    "geriInvalidation",
    "geriActiveLevelKind",
    "geriActiveLevelPrice",
    "channelPivotAAtMs",
    "channelPivotAPrice",
    "channelPivotBAtMs",
    "channelPivotBPrice",
    "channelPivotCAtMs",
    "channelPivotCPrice",
    "channelObservedAtMs",
    "channelSlopePerBar",
    "channelWidth",
    "channelMaturity",
    "channelReasons",
    "geriN1Kind",
    "geriN1Price",
    "geriN2Kind",
    "geriN2Price",
    "geriN3Kind",
    "geriN3Price",
)


@dataclass(frozen=True, slots=True)
class TradingViewAssessment:
    """Price geometry calculated locally from one Alpaca market-data snapshot."""

    symbol: str
    data_as_of: datetime
    swing: object | None = None
    swing_trade: object | None = None
    swing_channel: object | None = None
    geri: object | None = None
    errors: Mapping[str, str] = field(default_factory=dict[str, str])


def project_tradingview_row(assessment: TradingViewAssessment) -> dict[str, str]:
    """Flatten calculated price geometry into the Pine CSV contract."""

    normalized = assessment.symbol.strip().upper()
    if not normalized:
        raise ValueError("TradingView assessment requires a symbol")

    swing = _mapping(assessment.swing)
    swing_metrics = _metrics(swing)
    swing_trade = _mapping(assessment.swing_trade)
    channel = _mapping(assessment.swing_channel)
    geri = _mapping(assessment.geri)
    drawable_geri_zone = geri if _drawable_geri_long_zone(geri) else {}
    geri_n1 = _geri_level(geri, 1)
    geri_n2 = _geri_level(geri, 2)
    geri_n3 = _geri_level(geri, 3)

    row = {
        "ticker": normalized,
        "modoNiveles": "MANUAL",
        "marcoCalculo": "DIARIO FIJO",
        "zonaLowManual": _number(swing_metrics.get("entry_zone_low")),
        "zonaHighManual": _number(swing_metrics.get("entry_zone_high")),
        "soporteManual": _number(
            swing_metrics.get("structural_support", swing_metrics.get("support"))
        ),
        "invalidationManual": _number(swing_metrics.get("invalidation")),
        "resistenciaManual": _number(swing_metrics.get("resistance")),
        "liquidezManual": _number(swing_metrics.get("liquidity_high")),
        "avwapPivotManual": _number(swing_metrics.get("pivot_low_avwap")),
        "avwapBreakoutManual": _number(swing_metrics.get("breakout_avwap")),
        "extendedTarget": _number(swing_trade.get("extended_target")),
        "asOf": assessment.data_as_of.isoformat().replace("+00:00", "Z"),
        "fuenteDatos": "ALPACA_DIRECTO",
        "swingSma20": _number(swing_metrics.get("daily_sma20")),
        "swingSma50": _number(swing_metrics.get("daily_sma50")),
        "swingTarget": _number(
            swing_metrics.get("target_2r", swing_metrics.get("target"))
        ),
        "pivotLowAnchorAt": _text(swing_metrics.get("pivot_low_anchor_at")),
        "breakoutAnchorAt": _text(swing_metrics.get("breakout_anchor_at")),
        "stImpulseLow": _number(swing_trade.get("impulse_low")),
        "stImpulseHigh": _number(swing_trade.get("impulse_high")),
        "stImpulseLowAt": _text(swing_trade.get("impulse_low_at")),
        "stImpulseHighAt": _text(swing_trade.get("impulse_high_at")),
        "stFib618": _number(swing_trade.get("fibonacci_618")),
        "stFib50": _number(swing_trade.get("fibonacci_50")),
        "stFib1618": _number(swing_trade.get("fibonacci_1618")),
        "stZoneLow": _number(swing_trade.get("zone_low")),
        "stZoneHigh": _number(swing_trade.get("zone_high")),
        "stSupport20": _number(swing_trade.get("support_20d")),
        "stResistance20": _number(swing_trade.get("resistance_20d")),
        "stInvalidation": _number(swing_trade.get("invalidation")),
        "stPrimaryTarget": _number(swing_trade.get("primary_target")),
        "stExtendedTarget": _number(swing_trade.get("extended_target")),
        "channelZoneLow": _number(channel.get("zone_low")),
        "channelZoneHigh": _number(channel.get("zone_high")),
        "channelSupport": _number(channel.get("support")),
        "channelMiddle": _number(channel.get("middle")),
        "channelResistance": _number(channel.get("resistance")),
        "channelInvalidation": _number(channel.get("invalidation")),
        "geriZoneLow": _number(drawable_geri_zone.get("zone_low")),
        "geriZoneHigh": _number(drawable_geri_zone.get("zone_high")),
        "geriInvalidation": _number(drawable_geri_zone.get("invalidation")),
        "geriActiveLevelKind": _text(geri.get("active_level_kind")),
        "geriActiveLevelPrice": _number(geri.get("active_level_price")),
        "channelPivotAAtMs": _epoch_millis(channel.get("pivot_a_at")),
        "channelPivotAPrice": _number(channel.get("pivot_a_price")),
        "channelPivotBAtMs": _epoch_millis(channel.get("pivot_b_at")),
        "channelPivotBPrice": _number(channel.get("pivot_b_price")),
        "channelPivotCAtMs": _epoch_millis(channel.get("pivot_c_at")),
        "channelPivotCPrice": _number(channel.get("pivot_c_price")),
        "channelObservedAtMs": _epoch_millis(channel.get("occurred_at")),
        "channelSlopePerBar": _number(channel.get("slope_per_bar")),
        "channelWidth": _number(channel.get("width")),
        "channelMaturity": _text(channel.get("maturity")),
        "channelReasons": _joined_text(channel.get("reasons")),
        "geriN1Kind": _text(geri_n1.get("kind")),
        "geriN1Price": _number(geri_n1.get("price")),
        "geriN2Kind": _text(geri_n2.get("kind")),
        "geriN2Price": _number(geri_n2.get("price")),
        "geriN3Kind": _text(geri_n3.get("kind")),
        "geriN3Price": _number(geri_n3.get("price")),
    }
    return {column: row[column] for column in TRADINGVIEW_COLUMNS}


async def calculate_tradingview_assessments(
    symbols: Sequence[str],
    *,
    settings: AppSettings | None = None,
    as_of: datetime | None = None,
) -> tuple[TradingViewAssessment, ...]:
    """Fetch Alpaca bars once and run the configured pure geometry engines locally."""

    normalized = _symbols(symbols)
    resolved_settings = settings or AppSettings()
    now = (as_of or datetime.now(UTC)).astimezone(UTC)
    daily, fifteen = await _fetch_completed_bars(
        normalized,
        settings=resolved_settings,
        as_of=now,
    )
    assembly = MarketBotAssembly.from_settings(resolved_settings)
    return tuple(
        _calculate_symbol(
            symbol,
            daily_bars=daily.get(symbol, ()),
            fifteen_bars=fifteen.get(symbol, ()),
            assembly=assembly,
            as_of=now,
        )
        for symbol in normalized
    )


def _calculate_symbol(
    symbol: str,
    *,
    daily_bars: tuple[MarketBar, ...],
    fifteen_bars: tuple[MarketBar, ...],
    assembly: MarketBotAssembly,
    as_of: datetime,
) -> TradingViewAssessment:
    errors: dict[str, str] = {}
    if not daily_bars:
        errors["alpaca_daily"] = "no completed 1Day bars"
    if not fifteen_bars:
        errors["alpaca_15m"] = "no completed regular-session 15Min bars"
    if not daily_bars or not fifteen_bars:
        return TradingViewAssessment(symbol=symbol, data_as_of=as_of, errors=errors)

    price_bar = fifteen_bars[-1]
    price = price_bar.close
    price_at = price_bar.timestamp + timedelta(minutes=15)
    calculation_at = max(price_at, daily_bars[-1].timestamp)
    swing: AnalysisResult | None = None
    channel: SwingChannelAssessment | None = None
    geri: GeriAssessment | None = None
    swing_trade: SwingTradeAssessment | None = None

    try:
        swing = assembly.build_swing().analyze(
            SwingContext(
                symbol=symbol,
                as_of=calculation_at,
                price=price,
                daily_bars=daily_bars[-120:],
                intraday_bars=fifteen_bars[-160:],
            )
        )
    except ValueError as error:
        errors["swing"] = _reason(error)

    four_hour = _four_hour_bars(fifteen_bars)
    if not four_hour:
        errors["alpaca_4h"] = "not enough complete RTH 15Min segments"
    else:
        try:
            channel = assembly.build_swing_channel_4h().analyze(
                SwingChannel4HContext(
                    symbol=symbol,
                    bars=four_hour[-60:],
                    current_price=price,
                    daily_swing=swing,
                )
            )
        except ValueError as error:
            errors["swing_channel_4h"] = _reason(error)

        try:
            geri = assembly.build_4hgeri().analyze(
                Swing4HGeriContext(
                    symbol=symbol,
                    bars=four_hour[-60:],
                    current_price=price,
                    confirmation_bars=fifteen_bars[-32:],
                    daily_swing=swing,
                    as_of=calculation_at,
                    current_price_at=price_at,
                )
            )
        except ValueError as error:
            errors["geri_4h"] = _reason(error)

    swing_trade_engine = assembly.build_swing_trade()
    swing_trade_context = SwingTradeContext(
        symbol=symbol,
        as_of=calculation_at,
        current_price=price,
        daily_bars=daily_bars[-120:],
        geri=geri,
        confirmation_bars=fifteen_bars[-160:],
        current_price_at=price_at,
    )
    try:
        swing_trade = swing_trade_engine.analyze(swing_trade_context)
    except ValueError as error:
        if str(error) != "SwingTrade LONG impulse requires low before high":
            errors["swing_trade"] = _reason(error)
        else:
            try:
                swing_trade = swing_trade_engine.analyze_geometry(swing_trade_context)
            except ValueError as geometry_error:
                errors["swing_trade"] = _reason(geometry_error)

    return TradingViewAssessment(
        symbol=symbol,
        data_as_of=price_at,
        swing=swing,
        swing_trade=swing_trade,
        swing_channel=channel,
        geri=geri,
        errors=errors,
    )


async def _fetch_completed_bars(
    symbols: tuple[str, ...],
    *,
    settings: AppSettings,
    as_of: datetime,
) -> tuple[dict[str, tuple[MarketBar, ...]], dict[str, tuple[MarketBar, ...]]]:
    if not settings.alpaca_configured:
        raise ValueError("Alpaca market-data credentials are not configured")
    assert settings.alpaca_api_key_id is not None
    assert settings.alpaca_api_secret_key is not None
    transport = HttpxTransport(timeout_seconds=45)
    client = AlpacaRestClient(
        api_key_id=settings.alpaca_api_key_id.get_secret_value(),
        api_secret_key=settings.alpaca_api_secret_key.get_secret_value(),
        base_url=str(cast(object, settings.alpaca_data_base_url)),
        feed=settings.alpaca_data_feed,
        adjustment=settings.alpaca_adjustment,
        transport=transport,
    )
    normalizer = AlpacaEventNormalizer(feed=settings.alpaca_data_feed)
    try:
        daily_raw, fifteen_raw = await asyncio.gather(
            _fetch_batched(
                client,
                symbols,
                timeframe="1Day",
                start=as_of - timedelta(days=550),
                end=as_of,
                batch_size=settings.alpaca_rest_batch_size,
            ),
            _fetch_batched(
                client,
                symbols,
                timeframe="15Min",
                start=as_of - timedelta(days=90),
                end=as_of,
                batch_size=settings.alpaca_rest_batch_size,
            ),
        )
    finally:
        await transport.close()

    daily: dict[str, tuple[MarketBar, ...]] = {}
    fifteen: dict[str, tuple[MarketBar, ...]] = {}
    for symbol in symbols:
        normalized_daily = tuple(
            cast(MarketBar, normalizer.rest_bar(symbol, "1Day", item).envelope.payload)
            for item in daily_raw.get(symbol, ())
        )
        normalized_fifteen = tuple(
            cast(MarketBar, normalizer.rest_bar(symbol, "15Min", item).envelope.payload)
            for item in fifteen_raw.get(symbol, ())
        )
        daily[symbol] = _unique_bars(
            bar for bar in normalized_daily if _daily_bar_completed(bar, as_of=as_of)
        )
        fifteen[symbol] = _unique_bars(
            bar
            for bar in normalized_fifteen
            if is_regular_session(bar.timestamp)
            and bar.timestamp + timedelta(minutes=15) <= as_of
        )
    return daily, fifteen


async def _fetch_batched(
    client: AlpacaRestClient,
    symbols: tuple[str, ...],
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    batch_size: int,
) -> dict[str, list[Mapping[str, object]]]:
    collected: dict[str, list[Mapping[str, object]]] = {symbol: [] for symbol in symbols}
    for offset in range(0, len(symbols), batch_size):
        batch = symbols[offset : offset + batch_size]
        fetched = await client.fetch_bars(
            batch,
            timeframe=timeframe,
            start=start,
            end=end,
        )
        for symbol in batch:
            collected[symbol].extend(fetched.get(symbol, ()))
    return collected


def _daily_bar_completed(bar: MarketBar, *, as_of: datetime) -> bool:
    session_date = bar.timestamp.astimezone(_NEW_YORK).date()
    close_at = datetime.combine(session_date, time(16), _NEW_YORK).astimezone(UTC)
    return close_at <= as_of


def _four_hour_bars(bars: tuple[MarketBar, ...]) -> tuple[MarketBar, ...]:
    aggregator = RegularSessionFourHourAggregator()
    result: list[MarketBar] = []
    for bar in bars:
        result.extend(aggregator.add(bar))
    return tuple(result)


def _unique_bars(values: Iterable[MarketBar]) -> tuple[MarketBar, ...]:
    by_timestamp = {bar.timestamp: bar for bar in values}
    return tuple(by_timestamp[key] for key in sorted(by_timestamp))


def _symbols(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip().upper() for value in values if value.strip()))
    if not normalized:
        raise ValueError("at least one TradingView ticker is required")
    return normalized


def _reason(error: ValueError) -> str:
    return str(error) or type(error).__name__


def _price_above_invalidation(payload: Mapping[str, object]) -> bool:
    if not payload:
        return False
    price = _decimal(payload.get("current_price"))
    invalidation = _decimal(payload.get("invalidation"))
    return price is None or invalidation is None or price > invalidation


def _drawable_geri_long_zone(payload: Mapping[str, object]) -> bool:
    kind = _text(payload.get("active_level_kind"))
    side = _text(payload.get("trade_side"))
    return (
        kind == "SUPPORT"
        and side != "SHORT"
        and _price_above_invalidation(payload)
    )


def _decimal(value: object) -> Decimal | None:
    raw = _raw(value)
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _epoch_millis(value: object) -> str:
    raw = _raw(value)
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return "0"
    else:
        return "0"
    if parsed.tzinfo is None:
        return "0"
    return str(int(parsed.timestamp() * 1000))


def _metrics(payload: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, (list, tuple)):
        return result
    for raw in cast(list[object] | tuple[object, ...], raw_metrics):
        item = _mapping(raw)
        name = item.get("name")
        if isinstance(name, str):
            result[name] = item.get("value")
    return result


def _geri_level(payload: Mapping[str, object], sequence: int) -> dict[str, object]:
    raw_levels = payload.get("levels")
    if not isinstance(raw_levels, (list, tuple)):
        return {}
    for raw in cast(list[object] | tuple[object, ...], raw_levels):
        level = _mapping(raw)
        if str(level.get("sequence")) == str(sequence):
            return level
    return {}


def _mapping(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {str(key): item for key, item in source.items()}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            source = cast(Mapping[object, object], dumped)
            return {str(key): item for key, item in source.items()}
    return {}


def _raw(value: object) -> object:
    return getattr(value, "value", value)


def _text(value: object) -> str:
    raw = _raw(value)
    if raw is None or raw == "":
        return "SIN_DATO"
    return str(raw)


def _joined_text(value: object) -> str:
    raw = _raw(value)
    if isinstance(raw, (list, tuple)):
        values = cast(list[object] | tuple[object, ...], raw)
        rendered = "|".join(
            str(_raw(item)) for item in values if _raw(item) not in {None, ""}
        )
        return rendered or "SIN_DATO"
    return _text(raw)


def _number(value: object) -> str:
    raw = _raw(value)
    if raw is None or isinstance(raw, bool) or raw == "":
        return "0"
    return str(raw)
