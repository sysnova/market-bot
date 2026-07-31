"""PostgreSQL adapter for immutable LONG portfolio alerts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import LocalAlert
from app.persistence import LongPortfolioAlertRecord, PersistenceUnitOfWork


class PostgresLongPortfolioAlertStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_ready(self) -> bool:
        async with self._session_factory() as session:
            relation = await session.scalar(
                text("select to_regclass('market_bot.long_portfolio_alerts')")
            )
            return relation is not None

    async def save(self, alert: LocalAlert) -> bool:
        metrics = {item.name: item.value for item in alert.metrics}
        record = LongPortfolioAlertRecord(
            id=alert.alert_id,
            deduplication_key=alert.deduplication_key,
            symbol=alert.symbol,
            created_at=alert.created_at,
            expires_at=alert.expires_at,
            rule_version=_string(metrics, "long_portfolio_rule_version"),
            horizon_end=date.fromisoformat(_string(metrics, "long_horizon_end")),
            current_price=_decimal(metrics, "current_price"),
            buy_zone_low=_decimal(metrics, "buy_zone_low"),
            buy_zone_high=_decimal(metrics, "buy_zone_high"),
            invalidation=_decimal(metrics, "invalidation"),
            target_weight_percent=_decimal(metrics, "target_weight_percent"),
            target_capital_usd=_decimal(metrics, "target_capital_usd"),
            tranche_percent=_decimal(metrics, "suggested_tranche_percent"),
            tranche_usd=_decimal(metrics, "suggested_tranche_usd"),
            suggested_whole_shares=_decimal(metrics, "suggested_whole_shares"),
            score=alert.score,
            reasons=list(alert.reasons),
            payload=alert.model_dump(mode="json"),
            persisted_at=alert.created_at,
        )
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            return await unit.long_portfolio_alerts.add(record)

    async def recent(self, *, limit: int = 25) -> tuple[LocalAlert, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._session_factory() as session:
            records = await session.scalars(
                select(LongPortfolioAlertRecord)
                .order_by(LongPortfolioAlertRecord.created_at.desc())
                .limit(limit)
            )
            return tuple(
                LocalAlert.model_validate(record.payload, strict=False)
                for record in reversed(records.all())
            )


def _decimal(metrics: dict[str, object], name: str) -> Decimal:
    value = metrics.get(name)
    if not isinstance(value, Decimal):
        raise ValueError(f"alert metric {name} must be Decimal")
    return value


def _string(metrics: dict[str, object], name: str) -> str:
    value = metrics.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"alert metric {name} must be a non-empty string")
    return value
