"""Persistence port for entry opportunities."""

from __future__ import annotations

from typing import Protocol

from app.contracts import EntryWatchTransition

from .models import EntryWatch


class EntryWatchStore(Protocol):
    async def load_active(self, symbol: str) -> EntryWatch | None: ...

    async def load_latest(self, symbol: str) -> EntryWatch | None: ...

    async def create(
        self, watch: EntryWatch, transition: EntryWatchTransition
    ) -> None: ...

    async def transition(
        self, watch: EntryWatch, transition: EntryWatchTransition
    ) -> None: ...
