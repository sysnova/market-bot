"""Bounded in-memory order-flow risk detection for open holdings."""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast

from pydantic import BaseModel

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    EventEnvelope,
    LocalAlert,
    NamedValue,
    PatternDirection,
)


@dataclass(frozen=True, slots=True)
class PortfolioFlowPolicy:
    window: timedelta = timedelta(minutes=3)
    cooldown: timedelta = timedelta(minutes=5)
    minimum_trades: int = 20
    minimum_volume: Decimal = Decimal("1000")
    sell_ratio: Decimal = Decimal("0.70")
    buy_ratio: Decimal = Decimal("0.70")
    minimum_drop_percent: Decimal = Decimal("0.30")
    minimum_rise_percent: Decimal = Decimal("0.30")
    block_size: Decimal = Decimal("1000")


@dataclass(frozen=True, slots=True)
class FlowTrade:
    timestamp: datetime
    price: Decimal
    size: Decimal
    is_sell: bool
    is_buy: bool


class PortfolioFlowEngineV1:
    """Original bounded selling-pressure protection rules."""

    engine_version = "1.0.0"

    def __init__(self, policy: PortfolioFlowPolicy | None = None) -> None:
        self._policy = policy or PortfolioFlowPolicy()
        self._quotes: dict[str, tuple[Decimal, Decimal]] = {}
        self._trades: dict[str, deque[FlowTrade]] = defaultdict(deque)
        self._last_alert: dict[tuple[str, str], datetime] = {}

    def ingest(self, envelope: EventEnvelope, *, now: datetime) -> LocalAlert | None:
        raw_payload = envelope.payload
        if isinstance(raw_payload, BaseModel):
            payload = raw_payload.model_dump(mode="python")
        elif isinstance(raw_payload, Mapping):
            payload = cast("Mapping[str, object]", raw_payload)
        else:
            return None
        symbol = str(payload.get("symbol", "")).strip().upper()
        if not symbol:
            return None
        if envelope.event_type == "market.quote.received":
            self._quotes[symbol] = (
                Decimal(str(payload["bid_price"])),
                Decimal(str(payload["ask_price"])),
            )
            return None
        if envelope.event_type != "market.trade.received" or symbol not in self._quotes:
            return None
        price = Decimal(str(payload["price"]))
        size = Decimal(str(payload["size"]))
        bid, ask = self._quotes[symbol]
        trades = self._trades[symbol]
        trades.append(self._trade(envelope.occurred_at, price, size, bid=bid, ask=ask))
        cutoff = now - self._policy.window
        while trades and trades[0].timestamp < cutoff:
            trades.popleft()
        return self._evaluate(symbol, trades, now)

    @staticmethod
    def _trade(
        timestamp: datetime,
        price: Decimal,
        size: Decimal,
        *,
        bid: Decimal,
        ask: Decimal,
    ) -> FlowTrade:
        del ask
        return FlowTrade(timestamp, price, size, price <= bid, False)

    def _evaluate(self, symbol: str, trades: deque[FlowTrade], now: datetime) -> LocalAlert | None:
        if len(trades) < self._policy.minimum_trades:
            return None
        volume = sum((item.size for item in trades), Decimal())
        sell_volume = sum((item.size for item in trades if item.is_sell), Decimal())
        sell_ratio = sell_volume / volume if volume else Decimal()
        drop = (trades[0].price - trades[-1].price) / trades[0].price * 100
        if (
            volume < self._policy.minimum_volume
            or sell_ratio < self._policy.sell_ratio
            or drop < self._policy.minimum_drop_percent
        ):
            return None
        return self._selling_alert(symbol, trades, now, volume, sell_ratio, drop)

    def _selling_alert(
        self,
        symbol: str,
        trades: deque[FlowTrade],
        now: datetime,
        volume: Decimal,
        sell_ratio: Decimal,
        drop: Decimal,
    ) -> LocalAlert | None:
        side = "sell"
        previous = self._last_alert.get((symbol, side))
        if previous is not None and now - previous < self._policy.cooldown:
            return None
        self._last_alert[(symbol, side)] = now
        blocks = sum(1 for item in trades if item.is_sell and item.size >= self._policy.block_size)
        score = min(Decimal("100"), sell_ratio * 100 + drop * 5)
        analysis = AnalysisResult(
            engine_id="portfolio-flow",
            engine_version=self.engine_version,
            symbol=symbol,
            horizon=AnalysisHorizon.INTRADAY,
            as_of=now,
            verdict=AnalysisVerdict.CAUTION,
            direction=PatternDirection.BEARISH,
            score=score,
            confidence=min(Decimal("1"), sell_ratio),
            reasons=("presión vendedora", "aceleración de ventas", "pérdida de precio"),
            metrics=self._metrics(trades[-1].price, volume, sell_ratio, drop, blocks),
            source_event_ids=(),
            context_hash="sha256:"
            + hashlib.sha256(
                f"{symbol}:{now.isoformat()}:{volume}:{sell_ratio}:{drop}".encode()
            ).hexdigest(),
        )
        return LocalAlert(
            symbol=symbol,
            created_at=now,
            severity=AlertSeverity.CRITICAL,
            title=f"PROTECT {symbol}",
            message=(
                f"Presión vendedora: {sell_ratio * 100:.1f}% del volumen; "
                f"precio {drop:.2f}% abajo en {self._policy.window.seconds // 60} min."
            ),
            horizons=(AnalysisHorizon.INTRADAY,),
            component_analysis_ids=(analysis.analysis_id,),
            component_analyses=(analysis,),
            metrics=analysis.metrics,
            score=score,
            reasons=("presión vendedora fuerte", "revisar protección de la posición"),
            deduplication_key=f"portfolio-protect:{symbol}:{int(now.timestamp()) // 300}",
            kind=AlertKind.PORTFOLIO_PROTECT,
            expires_at=now + self._policy.cooldown,
        )

    @staticmethod
    def _metrics(
        price: Decimal, volume: Decimal, sell_ratio: Decimal, drop: Decimal, blocks: int
    ) -> tuple[NamedValue, ...]:
        return (
            NamedValue(name="current_price", value=price),
            NamedValue(name="window_volume", value=volume),
            NamedValue(name="sell_volume_percent", value=sell_ratio * 100),
            NamedValue(name="price_drop_percent", value=drop),
            NamedValue(name="large_sell_blocks", value=blocks),
        )
