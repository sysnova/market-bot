"""Deterministic in-memory Order Flow intelligence without execution side effects."""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.contracts.order_flow import (
    ORDER_FLOW_WINDOWS,
    MarketQuote,
    MarketTrade,
    MarketTradeCancel,
    MarketTradeCorrection,
    OrderFlowState,
    OrderFlowStateKind,
    OrderFlowTransition,
    OrderFlowWindow,
    TradeAggressor,
    WindowSeconds,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TEN_THOUSAND = Decimal("10000")


@dataclass(frozen=True, slots=True)
class OrderFlowPolicy:
    """Version-one thresholds; every calculation remains Decimal based."""

    quote_max_age: timedelta = timedelta(seconds=2)
    minimum_trades: int = 3
    minimum_volume: Decimal = Decimal("100")
    pressure_ratio: Decimal = Decimal("0.65")
    large_trade_size: Decimal = Decimal("1000")
    absorption_max_price_change_bps: Decimal = Decimal("2")
    absorption_minimum_trades: int = 10
    divergence_minimum_price_change_bps: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        if self.quote_max_age <= timedelta(0):
            raise ValueError("quote_max_age must be positive")
        if self.minimum_trades < 1 or self.absorption_minimum_trades < 1:
            raise ValueError("trade thresholds must be positive")
        if self.minimum_volume <= _ZERO or self.large_trade_size <= _ZERO:
            raise ValueError("volume thresholds must be positive")
        if not Decimal("0.5") < self.pressure_ratio <= _ONE:
            raise ValueError("pressure_ratio must be in (0.5, 1]")
        if (
            self.absorption_max_price_change_bps < _ZERO
            or self.divergence_minimum_price_change_bps < _ZERO
        ):
            raise ValueError("price thresholds cannot be negative")


@dataclass(frozen=True, slots=True)
class OrderFlowUpdate:
    """One analytical snapshot plus an optional material transition."""

    state: OrderFlowState
    aggressor: TradeAggressor | None
    transition: OrderFlowTransition | None


@dataclass(frozen=True, slots=True)
class _TradeRecord:
    trade: MarketTrade
    aggressor: TradeAggressor
    signed_volume: Decimal
    bid: Decimal | None
    ask: Decimal | None


@dataclass(slots=True)
class _SymbolBook:
    quote: MarketQuote | None = None
    trades: deque[_TradeRecord] = field(default_factory=lambda: deque[_TradeRecord]())
    trades_by_id: dict[str, _TradeRecord] = field(default_factory=lambda: dict[str, _TradeRecord]())
    cumulative_delta: Decimal = _ZERO
    last_trade_at: datetime | None = None
    last_trade_price: Decimal | None = None
    last_tick_aggressor: TradeAggressor | None = None
    latest_price: Decimal | None = None
    last_state: OrderFlowStateKind = OrderFlowStateKind.NEUTRAL


class OrderFlowEngine:
    """Compute causal, session-scoped operational microstructure states."""

    engine_version = "1.0.0"

    def __init__(self, policy: OrderFlowPolicy | None = None) -> None:
        self._policy = policy or OrderFlowPolicy()
        self._books: dict[str, _SymbolBook] = defaultdict(_SymbolBook)

    def ingest_quote(self, quote: MarketQuote) -> None:
        """Retain the newest quote; delayed older quotes never rewrite causal state."""

        book = self._books[quote.symbol]
        if book.quote is None or quote.occurred_at >= book.quote.occurred_at:
            book.quote = quote

    def ingest_trade(self, trade: MarketTrade) -> OrderFlowUpdate:
        """Classify and add one trade, then emit a compact snapshot."""

        book = self._books[trade.symbol]
        existing = book.trades_by_id.get(trade.trade_id)
        if existing is not None:
            return self._evaluate(
                trade.symbol,
                trade.occurred_at,
                aggressor=existing.aggressor,
                source_event_ids=(trade.event_id,),
                emit_transition=False,
            )
        if book.last_trade_at is not None and trade.occurred_at < book.last_trade_at:
            raise ValueError("trade is out of order for its symbol")

        quote = self._fresh_quote(book, trade.occurred_at)
        bid = quote.bid_price if quote is not None else None
        ask = quote.ask_price if quote is not None else None
        aggressor = self._classify(book, trade.price, bid=bid, ask=ask)
        record = self._record(trade, aggressor, bid=bid, ask=ask)
        book.trades.append(record)
        book.trades_by_id[trade.trade_id] = record
        book.cumulative_delta += record.signed_volume
        self._update_tick_state(book, trade.price)
        book.last_trade_at = trade.occurred_at
        book.latest_price = trade.price
        return self._evaluate(
            trade.symbol,
            trade.occurred_at,
            aggressor=aggressor,
            source_event_ids=(trade.event_id,),
        )

    def apply_correction(self, correction: MarketTradeCorrection) -> OrderFlowUpdate | None:
        """Reverse an observed print and insert its corrected replacement once."""

        book = self._books.get(correction.symbol)
        if book is None:
            return None
        original = book.trades_by_id.get(correction.original_trade_id)
        if original is None or correction.corrected_trade.trade_id in book.trades_by_id:
            return None

        self._remove_record(book, original)
        corrected = correction.corrected_trade
        aggressor = self._classify_price(
            corrected.price,
            bid=original.bid,
            ask=original.ask,
            previous_price=None,
            previous_tick=None,
        )
        record = self._record(corrected, aggressor, bid=original.bid, ask=original.ask)
        self._insert_record(book, record)
        book.trades_by_id[corrected.trade_id] = record
        book.cumulative_delta += record.signed_volume
        book.latest_price = corrected.price
        return self._evaluate(
            correction.symbol,
            correction.occurred_at,
            aggressor=aggressor,
            source_event_ids=(correction.event_id, corrected.event_id),
        )

    def apply_cancel(self, cancel: MarketTradeCancel) -> OrderFlowUpdate | None:
        """Reverse a known print; duplicate or unknown cancels are idempotent no-ops."""

        book = self._books.get(cancel.symbol)
        if book is None:
            return None
        record = book.trades_by_id.get(cancel.trade_id)
        if record is None:
            return None
        self._remove_record(book, record)
        return self._evaluate(
            cancel.symbol,
            cancel.occurred_at,
            aggressor=None,
            source_event_ids=(cancel.event_id,),
        )

    def snapshot(self, symbol: str, *, as_of: datetime) -> OrderFlowUpdate | None:
        """Return the latest state at ``as_of`` without inventing a market price."""

        if symbol not in self._books or self._books[symbol].latest_price is None:
            return None
        return self._evaluate(
            symbol,
            as_of,
            aggressor=None,
            source_event_ids=(),
            emit_transition=False,
        )

    def reset_symbol(self, symbol: str) -> None:
        """Start a clean intraday session for one symbol."""

        self._books.pop(symbol, None)

    def _evaluate(
        self,
        symbol: str,
        as_of: datetime,
        *,
        aggressor: TradeAggressor | None,
        source_event_ids: tuple[UUID, ...],
        emit_transition: bool = True,
    ) -> OrderFlowUpdate:
        book = self._books[symbol]
        self._prune_window(book, as_of)
        windows = tuple(self._window(book.trades, as_of, seconds) for seconds in ORDER_FLOW_WINDOWS)
        primary = windows[2]
        quote = book.quote
        quote_age_ms: Decimal | None = None
        quote_fresh = False
        if quote is not None:
            age = as_of - quote.occurred_at
            quote_age_ms = max(_ZERO, self._timedelta_decimal_seconds(age) * Decimal("1000"))
            quote_fresh = timedelta(0) <= age <= self._policy.quote_max_age

        total = windows[-1].total_volume
        unknown_ratio = windows[-1].unknown_volume / total if total else _ZERO
        data_quality = self._data_quality(
            quote_fresh=quote_fresh,
            unknown_ratio=unknown_ratio,
            trade_count=primary.trade_count,
        )
        kind = self._state_kind(windows)
        confidence = self._confidence(primary, data_quality)
        reasons = (kind.value.lower(),)
        current_price = book.latest_price
        if current_price is None:
            raise RuntimeError("cannot evaluate Order Flow without a market price")
        context_hash = self._context_hash(
            symbol=symbol,
            as_of=as_of,
            kind=kind,
            cumulative_delta=book.cumulative_delta,
            windows=windows,
        )
        state = OrderFlowState(
            state_id=_stable_uuid7(as_of, f"state:{context_hash}"),
            symbol=symbol,
            occurred_at=as_of,
            engine_version=self.engine_version,
            state=kind,
            current_price=current_price,
            mid_price=quote.mid_price if quote is not None else None,
            cumulative_delta=book.cumulative_delta,
            confidence=confidence,
            data_quality=data_quality,
            quote_age_ms=quote_age_ms,
            quote_fresh=quote_fresh,
            unknown_trade_ratio=unknown_ratio,
            windows=windows,
            reasons=reasons,
            source_event_ids=source_event_ids,
            context_hash=context_hash,
        )
        transition = None
        if emit_transition and kind is not book.last_state:
            transition = OrderFlowTransition(
                transition_id=_stable_uuid7(
                    as_of,
                    f"transition:{context_hash}:{book.last_state.value}:{kind.value}",
                ),
                state_id=state.state_id,
                symbol=symbol,
                occurred_at=as_of,
                engine_version=self.engine_version,
                previous_state=book.last_state,
                state=kind,
                confidence=confidence,
                current_price=current_price,
                reasons=reasons,
                context_hash=context_hash,
            )
            book.last_state = kind
        return OrderFlowUpdate(state=state, aggressor=aggressor, transition=transition)

    def _state_kind(self, windows: tuple[OrderFlowWindow, ...]) -> OrderFlowStateKind:
        five = windows[1]
        primary = windows[2]
        sixty = windows[3]
        classified = primary.buy_volume + primary.sell_volume
        if (
            primary.trade_count < self._policy.minimum_trades
            or classified < self._policy.minimum_volume
        ):
            return OrderFlowStateKind.NEUTRAL
        buy_ratio = primary.buy_volume / classified if classified else _ZERO
        sell_ratio = primary.sell_volume / classified if classified else _ZERO
        price_change = primary.price_change_bps
        divergence = self._policy.divergence_minimum_price_change_bps
        if price_change <= -divergence and primary.delta > _ZERO:
            return OrderFlowStateKind.BULLISH_DIVERGENCE
        if price_change >= divergence and primary.delta < _ZERO:
            return OrderFlowStateKind.BEARISH_DIVERGENCE
        if sixty.delta < _ZERO < five.delta and buy_ratio >= self._policy.pressure_ratio:
            return OrderFlowStateKind.SELLER_EXHAUSTION
        if sixty.delta > _ZERO > five.delta and sell_ratio >= self._policy.pressure_ratio:
            return OrderFlowStateKind.BUYER_EXHAUSTION
        if (
            primary.trade_count >= self._policy.absorption_minimum_trades
            and abs(price_change) <= self._policy.absorption_max_price_change_bps
        ):
            if buy_ratio >= self._policy.pressure_ratio:
                return OrderFlowStateKind.SELL_ABSORPTION
            if sell_ratio >= self._policy.pressure_ratio:
                return OrderFlowStateKind.BUY_ABSORPTION
        if buy_ratio >= self._policy.pressure_ratio:
            return OrderFlowStateKind.BUY_PRESSURE
        if sell_ratio >= self._policy.pressure_ratio:
            return OrderFlowStateKind.SELL_PRESSURE
        return OrderFlowStateKind.NEUTRAL

    @staticmethod
    def _confidence(window: OrderFlowWindow, data_quality: Decimal) -> Decimal:
        classified = window.buy_volume + window.sell_volume
        directional = abs(window.delta) / classified if classified else _ZERO
        return min(_ONE, directional * data_quality)

    @staticmethod
    def _data_quality(*, quote_fresh: bool, unknown_ratio: Decimal, trade_count: int) -> Decimal:
        quote_score = Decimal("0.4") if quote_fresh else _ZERO
        classification_score = Decimal("0.4") * (_ONE - unknown_ratio)
        sample_ratio = min(_ONE, Decimal(trade_count) / Decimal("20"))
        return min(_ONE, quote_score + classification_score + Decimal("0.2") * sample_ratio)

    def _window(
        self, trades: deque[_TradeRecord], as_of: datetime, seconds: WindowSeconds
    ) -> OrderFlowWindow:
        cutoff = as_of - timedelta(seconds=seconds)
        records = tuple(item for item in trades if cutoff <= item.trade.occurred_at <= as_of)
        buy = self._volume(records, TradeAggressor.BUY)
        sell = self._volume(records, TradeAggressor.SELL)
        neutral = self._volume(records, TradeAggressor.NEUTRAL)
        unknown = self._volume(records, TradeAggressor.UNKNOWN)
        total = buy + sell + neutral + unknown
        large_buy = sum(
            (
                item.trade.size
                for item in records
                if item.aggressor is TradeAggressor.BUY
                and item.trade.size >= self._policy.large_trade_size
            ),
            _ZERO,
        )
        large_sell = sum(
            (
                item.trade.size
                for item in records
                if item.aggressor is TradeAggressor.SELL
                and item.trade.size >= self._policy.large_trade_size
            ),
            _ZERO,
        )
        price_change = _ZERO
        if len(records) > 1:
            first = records[0].trade.price
            price_change = (records[-1].trade.price - first) / first * _TEN_THOUSAND
        return OrderFlowWindow(
            window_seconds=seconds,
            trade_count=len(records),
            buy_volume=buy,
            sell_volume=sell,
            neutral_volume=neutral,
            unknown_volume=unknown,
            delta=buy - sell,
            volume_velocity=total / Decimal(seconds),
            large_buy_volume=large_buy,
            large_sell_volume=large_sell,
            price_change_bps=price_change,
        )

    @staticmethod
    def _volume(records: tuple[_TradeRecord, ...], side: TradeAggressor) -> Decimal:
        return sum((item.trade.size for item in records if item.aggressor is side), _ZERO)

    def _fresh_quote(self, book: _SymbolBook, at: datetime) -> MarketQuote | None:
        quote = book.quote
        if quote is None:
            return None
        age = at - quote.occurred_at
        if timedelta(0) <= age <= self._policy.quote_max_age:
            return quote
        return None

    def _classify(
        self,
        book: _SymbolBook,
        price: Decimal,
        *,
        bid: Decimal | None,
        ask: Decimal | None,
    ) -> TradeAggressor:
        return self._classify_price(
            price,
            bid=bid,
            ask=ask,
            previous_price=book.last_trade_price,
            previous_tick=book.last_tick_aggressor,
        )

    @staticmethod
    def _classify_price(
        price: Decimal,
        *,
        bid: Decimal | None,
        ask: Decimal | None,
        previous_price: Decimal | None,
        previous_tick: TradeAggressor | None,
    ) -> TradeAggressor:
        if bid is not None and ask is not None:
            if price >= ask:
                return TradeAggressor.BUY
            if price <= bid:
                return TradeAggressor.SELL
            midpoint = (bid + ask) / Decimal("2")
            if price > midpoint:
                return TradeAggressor.BUY
            if price < midpoint:
                return TradeAggressor.SELL
            return TradeAggressor.NEUTRAL
        if previous_price is None:
            return TradeAggressor.UNKNOWN
        if price > previous_price:
            return TradeAggressor.BUY
        if price < previous_price:
            return TradeAggressor.SELL
        if previous_tick is TradeAggressor.BUY or previous_tick is TradeAggressor.SELL:
            return previous_tick
        return TradeAggressor.UNKNOWN

    @staticmethod
    def _record(
        trade: MarketTrade,
        aggressor: TradeAggressor,
        *,
        bid: Decimal | None,
        ask: Decimal | None,
    ) -> _TradeRecord:
        signed = _ZERO
        if aggressor is TradeAggressor.BUY:
            signed = trade.size
        elif aggressor is TradeAggressor.SELL:
            signed = -trade.size
        return _TradeRecord(
            trade=trade,
            aggressor=aggressor,
            signed_volume=signed,
            bid=bid,
            ask=ask,
        )

    @staticmethod
    def _update_tick_state(book: _SymbolBook, price: Decimal) -> None:
        previous = book.last_trade_price
        if previous is not None:
            if price > previous:
                book.last_tick_aggressor = TradeAggressor.BUY
            elif price < previous:
                book.last_tick_aggressor = TradeAggressor.SELL
        book.last_trade_price = price

    @staticmethod
    def _insert_record(book: _SymbolBook, record: _TradeRecord) -> None:
        records = [*book.trades, record]
        records.sort(key=lambda item: (item.trade.occurred_at, item.trade.trade_id))
        book.trades = deque(records)

    @staticmethod
    def _remove_record(book: _SymbolBook, record: _TradeRecord) -> None:
        book.cumulative_delta -= record.signed_volume
        book.trades_by_id.pop(record.trade.trade_id, None)
        book.trades = deque(
            item for item in book.trades if item.trade.trade_id != record.trade.trade_id
        )

    @staticmethod
    def _prune_window(book: _SymbolBook, as_of: datetime) -> None:
        cutoff = as_of - timedelta(seconds=ORDER_FLOW_WINDOWS[-1])
        while book.trades and book.trades[0].trade.occurred_at < cutoff:
            book.trades.popleft()

    @staticmethod
    def _timedelta_decimal_seconds(value: timedelta) -> Decimal:
        micros = value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds
        return Decimal(micros) / Decimal("1000000")

    @staticmethod
    def _context_hash(
        *,
        symbol: str,
        as_of: datetime,
        kind: OrderFlowStateKind,
        cumulative_delta: Decimal,
        windows: tuple[OrderFlowWindow, ...],
    ) -> str:
        metrics = ";".join(
            f"{item.window_seconds}:{item.trade_count}:{item.delta}:"
            f"{item.total_volume}:{item.price_change_bps}"
            for item in windows
        )
        raw = f"{symbol}:{as_of.isoformat()}:{kind.value}:{cumulative_delta}:{metrics}"
        return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _stable_uuid7(occurred_at: datetime, identity: str) -> UUID:
    timestamp_ms = int(occurred_at.timestamp() * 1_000) & ((1 << 48) - 1)
    random_bits = int.from_bytes(hashlib.sha256(identity.encode()).digest(), "big") & (
        (1 << 74) - 1
    )
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= ((random_bits >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= random_bits & ((1 << 62) - 1)
    return UUID(int=value)
