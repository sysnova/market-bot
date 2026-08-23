"""Persistence boundary for intraday paper opportunities."""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from app.contracts.intraday_opportunity import IntradayOpportunity, IntradayOpportunityEvent


class IntradayOpportunityStore(Protocol):
    """Atomic snapshot storage plus an append-only event and fill journal."""

    async def load_active(
        self, symbol: str, strategy_id: str
    ) -> IntradayOpportunity | None: ...

    async def list_session(self, session_date: date) -> tuple[IntradayOpportunity, ...]: ...

    async def source_event_seen(self, source_event_id: UUID) -> bool: ...

    async def save(
        self,
        opportunity: IntradayOpportunity,
        event: IntradayOpportunityEvent,
    ) -> None: ...
