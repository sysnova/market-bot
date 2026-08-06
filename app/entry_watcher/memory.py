"""In-memory entry-watch store used by isolated tests and local composition."""

from __future__ import annotations

from app.contracts import EntryWatchStatus, EntryWatchTransition

from .models import EntryWatch

_ACTIVE = {EntryWatchStatus.ARMED, EntryWatchStatus.IN_ZONE}


class InMemoryEntryWatchStore:
    def __init__(self) -> None:
        self.watches: dict[object, EntryWatch] = {}
        self.transitions: list[EntryWatchTransition] = []

    async def load_active(self, symbol: str) -> EntryWatch | None:
        normalized = symbol.strip().upper()
        matches = [
            watch
            for watch in self.watches.values()
            if watch.symbol == normalized and watch.status in _ACTIVE
        ]
        return max(matches, key=lambda watch: watch.armed_at) if matches else None

    async def load_latest(self, symbol: str) -> EntryWatch | None:
        normalized = symbol.strip().upper()
        matches = [watch for watch in self.watches.values() if watch.symbol == normalized]
        return max(matches, key=lambda watch: watch.updated_at) if matches else None

    async def create(self, watch: EntryWatch, transition: EntryWatchTransition) -> None:
        if await self.load_active(watch.symbol) is not None:
            raise RuntimeError(f"active entry watch already exists for {watch.symbol}")
        self.watches[watch.watch_id] = watch
        self.transitions.append(transition)

    async def transition(
        self, watch: EntryWatch, transition: EntryWatchTransition
    ) -> None:
        existing = self.watches.get(watch.watch_id)
        if existing is None:
            raise RuntimeError("entry watch does not exist")
        if transition.previous_status is not existing.status:
            raise RuntimeError("entry watch transition is stale")
        self.watches[watch.watch_id] = watch
        self.transitions.append(transition)
