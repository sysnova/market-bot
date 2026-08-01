"""Short-lived transaction boundary for persistence operations."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .repositories import (
    CheckpointRepository,
    EntryWatchRepository,
    HealthRepository,
    InboxRepository,
    LongPortfolioAlertRepository,
    OutboxRepository,
    PatreonCapsRepository,
)


class PersistenceUnitOfWork:
    """Own one database transaction and its repositories."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self.inbox: InboxRepository
        self.outbox: OutboxRepository
        self.checkpoints: CheckpointRepository
        self.health: HealthRepository
        self.entry_watches: EntryWatchRepository
        self.long_portfolio_alerts: LongPortfolioAlertRepository
        self.patreon_caps: PatreonCapsRepository

    async def __aenter__(self) -> PersistenceUnitOfWork:
        session = self._session_factory()
        self._session = session
        self.inbox = InboxRepository(session)
        self.outbox = OutboxRepository(session)
        self.checkpoints = CheckpointRepository(session)
        self.health = HealthRepository(session)
        self.entry_watches = EntryWatchRepository(session)
        self.long_portfolio_alerts = LongPortfolioAlertRepository(session)
        self.patreon_caps = PatreonCapsRepository(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._require_session()
        try:
            if exc_type is None:
                await session.commit()
            else:
                await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        await self._require_session().commit()

    async def rollback(self) -> None:
        await self._require_session().rollback()

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session
