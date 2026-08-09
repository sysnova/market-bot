"""Transactional outbox relay from local PostgreSQL to NATS JetStream."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.logging import get_logger
from app.contracts import EventEnvelope
from app.persistence import PersistenceUnitOfWork


class EventPublisher(Protocol):
    async def publish(self, subject: str, envelope: EventEnvelope) -> None: ...


class OutboxRelay:
    """Lease committed rows, publish outside the transaction, then record the result."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        *,
        clock: Callable[[], datetime],
        batch_size: int = 50,
        lease_duration: timedelta = timedelta(seconds=30),
        initial_backoff: timedelta = timedelta(seconds=1),
        maximum_backoff: timedelta = timedelta(minutes=5),
        poll_interval: float = 0.5,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if initial_backoff <= timedelta(0):
            raise ValueError("initial_backoff must be positive")
        if maximum_backoff < initial_backoff:
            raise ValueError("maximum_backoff must not be shorter than initial_backoff")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._session_factory = session_factory
        self._publisher = publisher
        self._clock = clock
        self._batch_size = batch_size
        self._lease_duration = lease_duration
        self._initial_backoff = initial_backoff
        self._maximum_backoff = maximum_backoff
        self._poll_interval = poll_interval

    async def drain_once(self) -> int:
        claimed_at = self._clock()
        async with PersistenceUnitOfWork(self._session_factory) as unit:
            events = await unit.outbox.claim_pending(
                limit=self._batch_size,
                now=claimed_at,
                lease_until=claimed_at + self._lease_duration,
            )

        published = 0
        for event in events:
            try:
                envelope = EventEnvelope.model_validate(event.payload, strict=False)
                await self._publisher.publish(event.subject, envelope)
            except Exception as error:
                failed_at = self._clock()
                async with PersistenceUnitOfWork(self._session_factory) as unit:
                    await unit.outbox.record_failure(
                        event.id,
                        error=f"{type(error).__name__}: {error}"[:2000],
                        available_at=failed_at + self._backoff(event.attempts),
                    )
                continue

            published_at = self._clock()
            async with PersistenceUnitOfWork(self._session_factory) as unit:
                await unit.outbox.mark_published(event.id, published_at=published_at)
            published += 1
        return published

    async def run(self) -> None:
        logger = get_logger("outbox-relay")
        while True:
            try:
                await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await logger.aexception(
                    "outbox_relay_batch_failed",
                    error_type=type(error).__name__,
                )
            await asyncio.sleep(self._poll_interval)

    def _backoff(self, attempts: int) -> timedelta:
        exponent = max(0, min(attempts - 1, 20))
        seconds = self._initial_backoff.total_seconds() * (2**exponent)
        return timedelta(seconds=min(seconds, self._maximum_backoff.total_seconds()))
