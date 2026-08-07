"""Symmetric buy/sell order-flow rules preserving v1 for rollback."""

from __future__ import annotations

import hashlib
from collections import deque
from datetime import datetime
from decimal import Decimal

from app.contracts import (
    AlertKind,
    AlertSeverity,
    AnalysisHorizon,
    AnalysisResult,
    AnalysisVerdict,
    LocalAlert,
    NamedValue,
    PatternDirection,
)

from .engine import FlowTrade, PortfolioFlowEngineV1


class PortfolioFlowEngineV2(PortfolioFlowEngineV1):
    """Add aggressive buy pressure while preserving v1 sell protection."""

    engine_version = "2.0.0"

    @staticmethod
    def _trade(
        timestamp: datetime,
        price: Decimal,
        size: Decimal,
        *,
        bid: Decimal,
        ask: Decimal,
    ) -> FlowTrade:
        is_sell = price <= bid
        is_buy = not is_sell and price >= ask
        return FlowTrade(timestamp, price, size, is_sell, is_buy)

    def _evaluate(self, symbol: str, trades: deque[FlowTrade], now: datetime) -> LocalAlert | None:
        if len(trades) < self._policy.minimum_trades:
            return None
        volume = sum((item.size for item in trades), Decimal())
        if volume < self._policy.minimum_volume:
            return None
        sell_volume = sum((item.size for item in trades if item.is_sell), Decimal())
        buy_volume = sum((item.size for item in trades if item.is_buy), Decimal())
        sell_ratio = sell_volume / volume if volume else Decimal()
        buy_ratio = buy_volume / volume if volume else Decimal()
        drop = (trades[0].price - trades[-1].price) / trades[0].price * 100
        rise = -drop
        if sell_ratio >= self._policy.sell_ratio and drop >= self._policy.minimum_drop_percent:
            return self._selling_alert(symbol, trades, now, volume, sell_ratio, drop)
        if buy_ratio >= self._policy.buy_ratio and rise >= self._policy.minimum_rise_percent:
            return self._buying_alert(symbol, trades, now, volume, buy_ratio, rise)
        return None

    def _buying_alert(
        self,
        symbol: str,
        trades: deque[FlowTrade],
        now: datetime,
        volume: Decimal,
        buy_ratio: Decimal,
        rise: Decimal,
    ) -> LocalAlert | None:
        side = "buy"
        previous = self._last_alert.get((symbol, side))
        if previous is not None and now - previous < self._policy.cooldown:
            return None
        self._last_alert[(symbol, side)] = now
        blocks = sum(1 for item in trades if item.is_buy and item.size >= self._policy.block_size)
        score = min(Decimal("100"), buy_ratio * 100 + rise * 5)
        analysis = AnalysisResult(
            engine_id="portfolio-flow",
            engine_version=self.engine_version,
            symbol=symbol,
            horizon=AnalysisHorizon.INTRADAY,
            as_of=now,
            verdict=AnalysisVerdict.WATCH,
            direction=PatternDirection.BULLISH,
            score=score,
            confidence=min(Decimal("1"), buy_ratio),
            reasons=("presión compradora", "aceleración de compras", "avance de precio"),
            metrics=self._buy_metrics(trades[-1].price, volume, buy_ratio, rise, blocks),
            source_event_ids=(),
            context_hash="sha256:"
            + hashlib.sha256(
                f"{symbol}:{now.isoformat()}:buy:{volume}:{buy_ratio}:{rise}".encode()
            ).hexdigest(),
        )
        return LocalAlert(
            symbol=symbol,
            created_at=now,
            severity=AlertSeverity.ACTION,
            title=f"AGGRESSIVE ENTRY WATCH {symbol}",
            message=(
                f"Presión compradora: {buy_ratio * 100:.1f}% del volumen; "
                f"precio {rise:.2f}% arriba en {self._policy.window.seconds // 60} min. "
                "Entrada agresiva: validar estructura y nivel de invalidación."
            ),
            horizons=(AnalysisHorizon.INTRADAY,),
            component_analysis_ids=(analysis.analysis_id,),
            component_analyses=(analysis,),
            metrics=analysis.metrics,
            score=score,
            reasons=("presión compradora fuerte", "entrada agresiva en observación"),
            deduplication_key=f"portfolio-flow-buy:{symbol}:{int(now.timestamp()) // 300}",
            kind=AlertKind.PORTFOLIO_FLOW_BUY,
            expires_at=now + self._policy.cooldown,
        )

    @staticmethod
    def _buy_metrics(
        price: Decimal, volume: Decimal, buy_ratio: Decimal, rise: Decimal, blocks: int
    ) -> tuple[NamedValue, ...]:
        return (
            NamedValue(name="current_price", value=price),
            NamedValue(name="window_volume", value=volume),
            NamedValue(name="buy_volume_percent", value=buy_ratio * 100),
            NamedValue(name="price_rise_percent", value=rise),
            NamedValue(name="large_buy_blocks", value=blocks),
        )
