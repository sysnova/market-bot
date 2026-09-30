"""Atomic, ordered Entry Opportunity command processing with bounded retries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.logging import get_logger
from app.contracts import EventEnvelope
from app.persistence import PersistenceUnitOfWork
from app.persistence.models import EntryOpportunityCommandRecord

from .entry_opportunity_store import PostgresEntryOpportunityStore


async def _ignore_health(healthy: bool) -> None:
    pass


class EntryOpportunityCommandIngress:
    """Keep a delivery in flight during database outages or a full durable queue."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._factory = session_factory
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def enqueue(self, envelope: EventEnvelope, *, command_type: str, symbol: str) -> None:
        failures = 0
        async with self._lock:
            while True:
                try:
                    async with PersistenceUnitOfWork(self._factory) as unit:
                        queue = unit.entry_opportunity_commands
                        if await queue.backlog_size() < 10000:
                            await queue.enqueue(
                                source_event_id=envelope.event_id,
                                source_subject=envelope.subject or "",
                                command_type=command_type,
                                symbol=symbol,
                                occurred_at=envelope.occurred_at,
                                payload=envelope.model_dump(mode="json"),
                            )
                            return
                except (DBAPIError, OSError, TimeoutError) as error:
                    failures += 1
                    delay = min(60, 2 ** min(failures - 1, 6))
                    await get_logger("entry-opportunity-commands").awarning(
                        "entry_opportunity_ingress_retry",
                        error_type=type(error).__name__,
                        retry_delay_seconds=delay,
                    )
                else:
                    failures = 0
                    delay = 5
                await self._sleep(delay)


class EntryOpportunityCommandProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        store: PostgresEntryOpportunityStore,
        apply: Callable[[str, EventEnvelope], Awaitable[None]],
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        report_health: Callable[[bool], Awaitable[None]] = _ignore_health,
    ) -> None:
        self._factory = session_factory
        self._store = store
        self._apply = apply
        self._clock = clock
        self._sleep = sleep
        self._report_health = report_health
        self._recovered = False
        self._command: EntryOpportunityCommandRecord | None = None

    async def drain_once(self) -> bool:
        self._command = None
        async with PersistenceUnitOfWork(self._factory) as unit:
            queue = unit.entry_opportunity_commands
            await queue.acquire_writer()
            if not self._recovered:
                await queue.requeue_processing(available_at=self._clock())
            command = await queue.claim_pending(now=self._clock())
            self._command = command
            if command is not None:
                envelope = EventEnvelope.model_validate(command.payload, strict=False)
                with self._store.bind(unit):
                    await self._apply(command.command_type, envelope)
                await queue.mark_processed(command.id, processed_at=self._clock())
        self._recovered = True
        return command is not None

    async def run(self) -> None:
        failures = 0
        logger = get_logger("entry-opportunity-commands")
        while True:
            try:
                async with asyncio.timeout(60):
                    worked = await self.drain_once()
                await self._report_health(True)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                delay = min(60, 2 ** min(failures - 1, 6))
                await self._report_health(False)
                command = self._command
                # Infrastructure outages do not exhaust a valid command's retry budget.
                infrastructure_failure = isinstance(error, (DBAPIError, OSError, TimeoutError))
                if command is not None and not infrastructure_failure:
                    try:
                        async with PersistenceUnitOfWork(self._factory) as unit:
                            await unit.entry_opportunity_commands.acquire_writer()
                            await unit.entry_opportunity_commands.mark_failed(
                                command.id,
                                error=type(error).__name__,
                                available_at=self._clock() + timedelta(seconds=delay),
                                terminal=command.attempts >= 5,
                            )
                    except DBAPIError, OSError, TimeoutError:
                        await self._sleep(delay)
                        continue
                    if command.attempts >= 5:
                        raise RuntimeError(f"command {command.id} failed five times") from error
                elif not infrastructure_failure:
                    raise
                await logger.awarning(
                    "entry_opportunity_processor_retry",
                    error_type=type(error).__name__,
                    retry_delay_seconds=delay,
                )
                await self._sleep(delay)
                continue
            failures = 0
            await self._sleep(0 if worked else 0.5)
