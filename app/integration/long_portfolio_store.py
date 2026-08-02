"""PostgreSQL adapter for immutable LONG portfolio alerts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contracts import LocalAlert
from app.long_portfolio_engine import LongPortfolioState
from app.persistence import (
    LongPortfolioAlertRecord,
    LongPortfolioStateRecord,
    PersistenceUnitOfWork,
)


class PostgresLongPortfolioAlertStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def is_ready(self) -> bool:
        async with self._session_factory() as session:
            relations = await session.execute(
                text(
                    """
                    select
                      to_regclass('market_bot.long_portfolio_alerts'),
                      to_regclass('market_bot.long_portfolio_states')
                    """
                )
            )
            alerts, states = relations.one()
            return alerts is not None and states is not None

    async def load_states(self, *, rule_version: str) -> tuple[LongPortfolioState, ...]:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            records = await unit.long_portfolio_states.load(rule_version=rule_version)
        return tuple(
            LongPortfolioState(
                symbol=record.symbol,
                rule_version=record.rule_version,
                qualified_sessions=tuple(
                    date.fromisoformat(value) for value in record.qualified_sessions
                ),
                last_emitted=record.last_emitted,
                updated_at=record.updated_at,
            )
            for record in records
        )

    async def save_state(self, state: LongPortfolioState) -> None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            await unit.long_portfolio_states.upsert(_state_record(state))

    async def save_evaluation(
        self, state: LongPortfolioState, alert: LocalAlert | None
    ) -> bool:
        """Commit confirmation state and an optional alert in one transaction."""

        async with PersistenceUnitOfWork(self._session_factory) as unit:
            await unit.long_portfolio_states.upsert(_state_record(state))
            if alert is None:
                return False
            return await unit.long_portfolio_alerts.add(_alert_record(alert))

    async def save(self, alert: LocalAlert) -> bool:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            return await unit.long_portfolio_alerts.add(_alert_record(alert))

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


def _state_record(state: LongPortfolioState) -> LongPortfolioStateRecord:
    return LongPortfolioStateRecord(
        rule_version=state.rule_version,
        symbol=state.symbol,
        qualified_sessions=[value.isoformat() for value in state.qualified_sessions],
        last_emitted=state.last_emitted,
        updated_at=state.updated_at,
    )


def _alert_record(alert: LocalAlert) -> LongPortfolioAlertRecord:
    metrics = {item.name: item.value for item in alert.metrics}
    return LongPortfolioAlertRecord(
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
