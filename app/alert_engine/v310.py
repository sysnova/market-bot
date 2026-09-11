"""Limit SHORT confirmations to the scope supplied by the assembly."""

from collections.abc import Collection
from datetime import datetime

from app.contracts import LocalAlert

from .v39 import AlertEngineV39


class AlertEngineV310(AlertEngineV39):
    engine_version = "3.10.0"
    requires_order_flow_short_scope = True

    def __init__(
        self,
        *args: object,
        short_symbols: Collection[str] = (),
        short_confirmation_enabled: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, short_confirmation_enabled=short_confirmation_enabled, **kwargs)
        self._short_symbols = frozenset(short_symbols)

    def _confirm_short(
        self, symbol: str, *, now: datetime, existing: LocalAlert | None
    ) -> LocalAlert | None:
        if symbol not in self._short_symbols:
            return None
        return super()._confirm_short(symbol, now=now, existing=existing)
