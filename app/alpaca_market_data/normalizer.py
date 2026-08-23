"""Pure Alpaca wire-message normalization."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from app.contracts import (
    MARKET_BAR_EVENT,
    MARKET_BAR_UPDATED_EVENT,
    BarTimeframe,
    EventEnvelope,
    MarketBar,
    NamedValue,
    market_bar_subject,
)
from app.contracts.order_flow import (
    MARKET_QUOTE_EVENT,
    MARKET_TRADE_CANCEL_EVENT,
    MARKET_TRADE_CORRECTION_EVENT,
    MARKET_TRADE_EVENT,
    MarketQuote,
    MarketTrade,
    MarketTradeCancel,
    MarketTradeCorrection,
)

_TYPE_NAMES = {
    "t": "trade",
    "q": "quote",
    "b": "bar",
    "u": "updated_bar",
    "d": "daily_bar",
    "s": "trading_status",
    "l": "luld",
    "c": "trade_correction",
    "x": "trade_cancel",
}


@dataclass(frozen=True, slots=True)
class Publication:
    """One transport-neutral publication produced from provider data."""

    subject: str
    envelope: EventEnvelope


class AlpacaEventNormalizer:
    """Normalize Alpaca REST and stock-stream records to stable v1 envelopes."""

    def __init__(self, *, feed: str) -> None:
        self._feed = feed

    def stream_message(self, raw: Mapping[str, object]) -> Publication:
        message_type = _required_text(raw, "T")
        kind = _TYPE_NAMES.get(message_type, f"raw_{_subject_token(message_type)}")
        symbol = _required_text(raw, "S")
        occurred_at = _timestamp(raw.get("t"))
        identity = {"message": raw, "feed": self._feed}
        event_id = _stable_uuid7(occurred_at, identity)

        if message_type == "t":
            payload = _trade(raw, symbol=symbol, occurred_at=occurred_at, event_id=event_id)
            event_type = MARKET_TRADE_EVENT
            subject_kind = "trade"
        elif message_type == "q":
            payload = _quote(raw, symbol=symbol, occurred_at=occurred_at, event_id=event_id)
            event_type = MARKET_QUOTE_EVENT
            subject_kind = "quote"
        elif message_type == "c":
            payload = _trade_correction(
                raw,
                symbol=symbol,
                occurred_at=occurred_at,
                event_id=event_id,
            )
            event_type = MARKET_TRADE_CORRECTION_EVENT
            subject_kind = "trade-correction"
        elif message_type == "x":
            payload = MarketTradeCancel(
                event_id=event_id,
                symbol=symbol,
                occurred_at=occurred_at,
                trade_id=_required_identifier(raw, "i"),
            )
            event_type = MARKET_TRADE_CANCEL_EVENT
            subject_kind = "trade-cancel"
        elif message_type in {"b", "u", "d"}:
            timeframe = "1Day" if message_type == "d" else "1Min"
            payload = _market_bar(
                raw,
                symbol=symbol,
                feed=self._feed,
                timeframe=timeframe,
                is_final=message_type not in {"u", "d"},
            )
            event_type = MARKET_BAR_UPDATED_EVENT if message_type == "u" else MARKET_BAR_EVENT
            subject_kind = "bar"
        else:
            payload = {
                "feed": self._feed,
                "message_type": message_type,
                "provider": "alpaca",
                "raw": _json_safe(raw),
                "symbol": symbol,
            }
            event_type = "market.provider_event.received"
            subject_kind = kind

        subject = f"market.data.{subject_kind}.{_subject_token(symbol)}"
        if isinstance(payload, MarketBar):
            subject = market_bar_subject(payload.timeframe, symbol)
        return self._publication(
            subject=subject,
            event_type=event_type,
            symbol=symbol,
            occurred_at=occurred_at,
            payload=payload,
            identity=identity,
            event_id=event_id,
        )

    def rest_bar(
        self,
        symbol: str,
        timeframe: str,
        raw: Mapping[str, object],
    ) -> Publication:
        occurred_at = _timestamp(raw.get("t"))
        payload = _market_bar(
            raw,
            symbol=symbol,
            feed=self._feed,
            timeframe=timeframe,
            is_final=True,
        )
        subject = market_bar_subject(payload.timeframe, symbol)
        return self._publication(
            subject=subject,
            event_type=MARKET_BAR_EVENT,
            symbol=symbol,
            occurred_at=occurred_at,
            payload=payload,
            identity={"bar": raw, "feed": self._feed, "timeframe": timeframe},
        )

    def snapshot(self, symbol: str, raw: Mapping[str, object]) -> Publication:
        timestamps = tuple(_nested_timestamps(raw))
        if not timestamps:
            raise ValueError("Alpaca snapshot has no observation timestamp")
        occurred_at = max(timestamps)
        payload: dict[str, object] = {
            "feed": self._feed,
            "provider": "alpaca",
            "symbol": symbol,
        }
        _optional_snapshot_trade(payload, "latest_trade", raw.get("latestTrade"))
        _optional_snapshot_quote(payload, "latest_quote", raw.get("latestQuote"))
        _optional_snapshot_bar(payload, "minute_bar", raw.get("minuteBar"), "1Min")
        _optional_snapshot_bar(payload, "daily_bar", raw.get("dailyBar"), "1Day")
        _optional_snapshot_bar(
            payload,
            "previous_daily_bar",
            raw.get("prevDailyBar"),
            "1Day",
        )
        return self._publication(
            subject=f"market.data.snapshot.{_subject_token(symbol)}",
            event_type="market.snapshot.received",
            symbol=symbol,
            occurred_at=occurred_at,
            payload=payload,
            identity={"snapshot": raw, "feed": self._feed},
        )

    def _publication(
        self,
        *,
        subject: str,
        event_type: str,
        symbol: str,
        occurred_at: datetime,
        payload: object,
        identity: object,
        event_id: UUID | None = None,
    ) -> Publication:
        return Publication(
            subject=subject,
            envelope=EventEnvelope(
                event_id=event_id or _stable_uuid7(occurred_at, identity),
                event_type=event_type,
                occurred_at=occurred_at,
                source="alpaca_market_data",
                subject=symbol,
                payload=payload,
                attributes=(
                    NamedValue(name="provider", value="alpaca"),
                    NamedValue(name="feed", value=self._feed),
                ),
            ),
        )


def _trade(
    raw: Mapping[str, object],
    *,
    symbol: str,
    occurred_at: datetime,
    event_id: UUID,
) -> MarketTrade:
    return MarketTrade(
        event_id=event_id,
        symbol=symbol,
        occurred_at=occurred_at,
        received_at=occurred_at,
        price=Decimal(_decimal_text(raw.get("p"))),
        size=Decimal(_decimal_text(raw.get("s"))),
        trade_id=_required_identifier(raw, "i"),
        exchange=_optional_text(raw.get("x")),
        tape=_optional_text(raw.get("z")),
        conditions=_text_sequence(raw.get("c")),
    )


def _quote(
    raw: Mapping[str, object],
    *,
    symbol: str,
    occurred_at: datetime,
    event_id: UUID,
) -> MarketQuote:
    return MarketQuote(
        event_id=event_id,
        symbol=symbol,
        occurred_at=occurred_at,
        received_at=occurred_at,
        ask_price=Decimal(_decimal_text(raw.get("ap"))),
        ask_size=Decimal(_decimal_text(raw.get("as"))),
        bid_price=Decimal(_decimal_text(raw.get("bp"))),
        bid_size=Decimal(_decimal_text(raw.get("bs"))),
        ask_exchange=_optional_text(raw.get("ax")),
        bid_exchange=_optional_text(raw.get("bx")),
        tape=_optional_text(raw.get("z")),
        conditions=_text_sequence(raw.get("c")),
    )


def _trade_correction(
    raw: Mapping[str, object],
    *,
    symbol: str,
    occurred_at: datetime,
    event_id: UUID,
) -> MarketTradeCorrection:
    corrected_event_id = _stable_uuid7(
        occurred_at,
        {"corrected_trade": raw, "symbol": symbol},
    )
    corrected = MarketTrade(
        event_id=corrected_event_id,
        symbol=symbol,
        occurred_at=occurred_at,
        received_at=occurred_at,
        price=Decimal(_decimal_text(raw.get("cp"))),
        size=Decimal(_decimal_text(raw.get("cs"))),
        trade_id=_required_identifier(raw, "ci"),
        exchange=_optional_text(raw.get("x")),
        tape=_optional_text(raw.get("z")),
        conditions=_text_sequence(raw.get("cc")),
    )
    return MarketTradeCorrection(
        event_id=event_id,
        symbol=symbol,
        occurred_at=occurred_at,
        original_trade_id=_required_identifier(raw, "oi"),
        corrected_trade=corrected,
    )


def _market_bar(
    raw: Mapping[str, object],
    *,
    symbol: str,
    feed: str,
    timeframe: str,
    is_final: bool,
) -> MarketBar:
    trade_count = raw.get("n")
    if trade_count is not None and (
        isinstance(trade_count, bool) or int(str(trade_count)) != Decimal(str(trade_count))
    ):
        raise ValueError("Alpaca bar trade count must be an integer")
    normalized_trade_count = int(str(trade_count)) if trade_count is not None else None
    volume = Decimal(_decimal_text(raw.get("v")))
    raw_vwap = raw.get("vw")
    vwap = Decimal(_decimal_text(raw_vwap)) if raw_vwap is not None else None
    if vwap == 0 and volume == 0 and normalized_trade_count in {None, 0}:
        vwap = None
    return MarketBar(
        symbol=symbol,
        timeframe=BarTimeframe(timeframe),
        timestamp=_timestamp(raw.get("t")),
        open=Decimal(_decimal_text(raw.get("o"))),
        high=Decimal(_decimal_text(raw.get("h"))),
        low=Decimal(_decimal_text(raw.get("l"))),
        close=Decimal(_decimal_text(raw.get("c"))),
        volume=volume,
        trade_count=normalized_trade_count,
        vwap=vwap,
        source="alpaca",
        feed=feed,
        is_final=is_final,
    )


def _optional_snapshot_trade(output: dict[str, object], key: str, value: object) -> None:
    raw = _mapping_or_none(value)
    if raw is not None:
        output[key] = {
            "price": _decimal_text(raw.get("p")),
            "size": _decimal_text(raw.get("s")),
            "timestamp": _timestamp(raw.get("t")).isoformat(),
        }


def _optional_snapshot_quote(output: dict[str, object], key: str, value: object) -> None:
    raw = _mapping_or_none(value)
    if raw is not None:
        output[key] = {
            "ask_price": _decimal_text(raw.get("ap")),
            "bid_price": _decimal_text(raw.get("bp")),
            "timestamp": _timestamp(raw.get("t")).isoformat(),
        }


def _optional_snapshot_bar(
    output: dict[str, object], key: str, value: object, timeframe: str
) -> None:
    raw = _mapping_or_none(value)
    if raw is None:
        return
    bar: dict[str, object] = {
        "timeframe": timeframe,
        "timestamp": _timestamp(raw.get("t")).isoformat(),
    }
    fields = (
        ("o", "open"),
        ("h", "high"),
        ("l", "low"),
        ("c", "close"),
        ("v", "volume"),
        ("vw", "vwap"),
        ("n", "trade_count"),
    )
    for source, target in fields:
        if raw.get(source) is not None:
            bar[target] = _decimal_text(raw.get(source))
    output[key] = bar


def _mapping_or_none(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast("Mapping[str, object]", value)


def _nested_timestamps(raw: Mapping[str, object]) -> Iterator[datetime]:
    for value in raw.values():
        nested = _mapping_or_none(value)
        if nested is not None and nested.get("t") is not None:
            yield _timestamp(nested.get("t"))


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Alpaca message requires timestamp t")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Alpaca timestamps must be timezone-aware")
    return parsed.astimezone(UTC)


def _decimal_text(value: object) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError("Alpaca numeric field is missing or invalid")
    try:
        return format(Decimal(str(value)), "f")
    except InvalidOperation as error:
        raise ValueError("Alpaca numeric field is invalid") from error


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Alpaca message requires {key}")
    return value


def _required_identifier(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError(f"Alpaca message requires {key}")
    return str(value).strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _text_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        items = cast("list[object] | tuple[object, ...]", value)
        return tuple(dict.fromkeys(str(item) for item in items if str(item).strip()))
    return ()


def _subject_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    if not token:
        raise ValueError("value cannot form a NATS subject token")
    return token


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, list | tuple):
        items = cast("list[object] | tuple[object, ...]", value)
        return [_json_safe(item) for item in items]
    if isinstance(value, float | Decimal):
        return _decimal_text(value)
    return value


def _stable_uuid7(occurred_at: datetime, identity: object) -> UUID:
    timestamp_ms = int(occurred_at.timestamp() * 1_000) & ((1 << 48) - 1)
    encoded = json.dumps(_json_safe(identity), sort_keys=True, separators=(",", ":"))
    random_bits = int.from_bytes(hashlib.sha256(encoded.encode()).digest(), "big") & ((1 << 74) - 1)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)
