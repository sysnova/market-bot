"""Keep a broken LONG structure eligible for independently confirmed SHORTs."""

from decimal import Decimal

from .v14 import SwingEngineV14

_INVALIDATED_LONG_STATES = frozenset({"STRUCTURE_INVALIDATED", "VOLATILITY_INVALIDATED"})


class SwingEngineV15(SwingEngineV14):
    """Separate a terminal LONG breakout lifecycle from current bearish evidence."""

    engine_version = "15.0.0"
    short_failed_breakout_states = (
        SwingEngineV14.short_failed_breakout_states | _INVALIDATED_LONG_STATES
    )

    def _short_thesis_broken(self, price: Decimal, metrics: dict[str, object]) -> bool:
        if str(metrics.get("failed_breakout_state", "NONE")) in _INVALIDATED_LONG_STATES:
            # The LONG lifecycle retains its first terminal state. Do not let that
            # historical break trigger a SHORT after price reclaims the old level.
            level = metrics.get("failed_breakout_level")
            if not isinstance(level, Decimal) or price >= level:
                return False
        return super()._short_thesis_broken(price, metrics)
