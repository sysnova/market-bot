"""PostgreSQL adapter for the Alert v3 decision checkpoint."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alert_engine import AlertEngineV3State, AlertEngineV31
from app.persistence import PersistenceUnitOfWork

_ENGINE_NAME = "alert"


class PostgresAlertDecisionStateStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        implementation_version: str = AlertEngineV31.engine_version,
    ) -> None:
        self._session_factory = session_factory
        self._implementation_version = implementation_version

    async def is_ready(self) -> bool:
        async with self._session_factory() as session:
            relation = await session.scalar(
                text("select to_regclass('market_bot.engine_decision_states')")
            )
            return relation is not None

    async def load(self) -> AlertEngineV3State | None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            record = await unit.engine_decision_states.load(
                _ENGINE_NAME,
                self._implementation_version,
            )
        if record is None:
            return None
        if (
            record.implementation_version != self._implementation_version
            or record.state_schema_version != "1.0.0"
        ):
            return None
        return AlertEngineV3State.model_validate(record.payload, strict=False)

    async def save(self, state: AlertEngineV3State) -> None:
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            await unit.engine_decision_states.upsert(
                engine_name=_ENGINE_NAME,
                implementation_version=self._implementation_version,
                state_schema_version=state.schema_version,
                payload=state.model_dump(mode="json"),
            )
