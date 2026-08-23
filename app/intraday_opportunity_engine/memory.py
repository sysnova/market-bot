"""Isolated in-memory store used by tests and local paper simulations."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.contracts.intraday_opportunity import (
    IntradayFill,
    IntradayOpportunity,
    IntradayOpportunityEvent,
    IntradayOpportunityStatus,
)


class InMemoryIntradayOpportunityStore:
    def __init__(self) -> None:
        self.opportunities: dict[UUID, IntradayOpportunity] = {}
        self.events: list[IntradayOpportunityEvent] = []
        self.fills: list[IntradayFill] = []

    async def load_active(
        self, symbol: str, strategy_id: str
    ) -> IntradayOpportunity | None:
        normalized_symbol = symbol.strip().upper()
        normalized_strategy = strategy_id.strip()
        matches = (
            item
            for item in self.opportunities.values()
            if item.symbol == normalized_symbol
            and item.strategy_id == normalized_strategy
            and item.status is IntradayOpportunityStatus.OPEN
        )
        return max(matches, key=lambda item: item.updated_at, default=None)

    async def list_session(self, session_date: date) -> tuple[IntradayOpportunity, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.opportunities.values()
                    if item.session_date == session_date
                ),
                key=lambda item: (item.opened_at, item.symbol, item.strategy_id),
            )
        )

    async def source_event_seen(self, source_event_id: UUID) -> bool:
        return any(item.source_event_id == source_event_id for item in self.events)

    async def save(
        self,
        opportunity: IntradayOpportunity,
        event: IntradayOpportunityEvent,
    ) -> None:
        active = await self.load_active(opportunity.symbol, opportunity.strategy_id)
        if active is not None and active.opportunity_id != opportunity.opportunity_id:
            raise RuntimeError(
                "active intraday opportunity already exists for "
                f"{opportunity.symbol}/{opportunity.strategy_id}"
            )
        if await self.source_event_seen(event.source_event_id):
            return
        self.opportunities[opportunity.opportunity_id] = opportunity
        self.events.append(event)
        if event.fill is not None:
            self.fills.append(event.fill)
