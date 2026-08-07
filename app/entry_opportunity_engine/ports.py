"""Persistence port for the Entry Opportunity engine."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.contracts import EntryOpportunity, EntryOpportunityEvent


class EntryOpportunityStore(Protocol):
    """Materialized opportunity state plus immutable lifecycle events."""

    async def load_active(self, symbol: str) -> EntryOpportunity | None: ...

    async def load_latest(self, symbol: str) -> EntryOpportunity | None: ...

    async def list_active(self) -> tuple[EntryOpportunity, ...]: ...

    async def event_seen(self, event_id: UUID) -> bool: ...

    async def save(
        self,
        opportunity: EntryOpportunity,
        event: EntryOpportunityEvent | None,
    ) -> None: ...
