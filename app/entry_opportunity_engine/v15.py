"""Restrict new SHORT entries while preserving every existing lifecycle."""

from collections.abc import Callable, Collection
from datetime import UTC, datetime

from app.contracts import AlertKind, EntryOpportunityEvent, LocalAlert

from .ports import EntryOpportunityStore
from .v14 import EntryOpportunityEngineV14


class EntryOpportunityEngineV15(EntryOpportunityEngineV14):
    engine_version = "15.0.0"
    requires_order_flow_short_scope = True

    def __init__(
        self,
        *,
        store: EntryOpportunityStore,
        short_symbols: Collection[str] = (),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(store=store, now=now)
        self._short_symbols = frozenset(short_symbols)

    async def ingest_alert(self, alert: LocalAlert) -> tuple[EntryOpportunityEvent, ...]:
        if (
            alert.kind is AlertKind.BEARISH_CONSENSUS
            and "short_entry_confirmed" in alert.reasons
            and alert.symbol not in self._short_symbols
        ):
            return ()
        return await super().ingest_alert(alert)
