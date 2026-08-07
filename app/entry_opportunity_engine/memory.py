"""In-memory store for the Entry Opportunity engine lifecycle."""

from __future__ import annotations

from uuid import UUID

from app.contracts import EntryOpportunity, EntryOpportunityEvent, EntryOpportunityStatus


class InMemoryEntryOpportunityStore:
    def __init__(self) -> None:
        self.opportunities: dict[UUID, EntryOpportunity] = {}
        self.events: list[EntryOpportunityEvent] = []

    async def load_active(self, symbol: str) -> EntryOpportunity | None:
        normalized = symbol.strip().upper()
        matches = [
            item
            for item in self.opportunities.values()
            if item.symbol == normalized and item.status is not EntryOpportunityStatus.CLOSED
        ]
        return max(matches, key=lambda item: item.updated_at) if matches else None

    async def load_latest(self, symbol: str) -> EntryOpportunity | None:
        normalized = symbol.strip().upper()
        matches = [item for item in self.opportunities.values() if item.symbol == normalized]
        return max(matches, key=lambda item: item.updated_at) if matches else None

    async def list_active(self) -> tuple[EntryOpportunity, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self.opportunities.values()
                    if item.status is not EntryOpportunityStatus.CLOSED
                ),
                key=lambda item: item.symbol,
            )
        )

    async def event_seen(self, event_id: UUID) -> bool:
        return any(item.event_id == event_id for item in self.events)

    async def save(
        self,
        opportunity: EntryOpportunity,
        event: EntryOpportunityEvent | None,
    ) -> None:
        active = await self.load_active(opportunity.symbol)
        if active is not None and active.opportunity_id != opportunity.opportunity_id:
            raise RuntimeError(f"active entry opportunity already exists for {opportunity.symbol}")
        self.opportunities[opportunity.opportunity_id] = opportunity
        if event is not None and not await self.event_seen(event.event_id):
            self.events.append(event)
