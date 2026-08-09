"""Process-local publication gate driven by completed universe warmups."""

from __future__ import annotations

from app.contracts import UniverseChanged


class UniverseWarmupGate:
    """Remain legacy-open until a coordinator starts sending universe snapshots."""

    def __init__(self) -> None:
        self._symbols: set[str] | None = None

    def activate(self, symbols: tuple[str, ...]) -> None:
        self._symbols = {value.strip().upper() for value in symbols if value.strip()}

    def apply(self, change: UniverseChanged) -> tuple[str, ...]:
        self.activate(change.symbols)
        return change.added_symbols

    def allows(self, symbol: str) -> bool:
        return self._symbols is None or symbol.strip().upper() in self._symbols
